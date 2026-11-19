"""Rework survival tests against a REAL throwaway git repo.

The fixture scripts a history the way a merge-commit workflow produces one:

    C0 (main)      f.txt = base-1..3
      |
      \\ feature: C1   f.txt += line-1..5   +  g.txt(3 lines)  +  h.txt(4 lines)
      |
    M  (main, --no-ff merge of feature)      <- merged_at
      |
    C2 (main, within 21d)  reworks line-1,2 of f.txt  +  deletes g.txt

Blaming at C2 (the rev `merged_at + 21d` resolves to), of the 12 lines PR C1
added: f.txt keeps 3 of 5 (2 reworked), g.txt is gone (3 reworked), h.txt keeps
all 4 (0 reworked) -> 5 reworked of 12. Every number below is hand-derived from
that history. No network; the merge SHA M is never counted as a survivor."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tempo import gitio
from tempo.jobs.rework import compute_pr_rework

UTC = timezone.utc

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

MERGED_AT = datetime(2026, 1, 3, tzinfo=UTC)
WINDOW_END = MERGED_AT + timedelta(days=21)  # 2026-01-24


def _run(repo, *args, when=None):
    env = os.environ.copy()
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=env
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace')}")
    return proc.stdout.decode(errors="replace").strip()


def _commit(repo, message, when):
    _run(repo, "-c", "commit.gpgsign=false", "commit", "-m", message, when=when)
    return _run(repo, "rev-parse", "HEAD")


_F_AFTER_C1 = (
    "base-1\nbase-2\nbase-3\n"
    "line-1-original\nline-2-original\nline-3-original\nline-4-original\nline-5-original\n"
)
_F_AFTER_C2 = (
    "base-1\nbase-2\nbase-3\n"
    "line-1-REWORKED\nline-2-REWORKED\nline-3-original\nline-4-original\nline-5-original\n"
)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(r)], capture_output=True, check=True)
    _run(r, "config", "user.email", "test@example.com")
    _run(r, "config", "user.name", "Test")
    _run(r, "config", "core.autocrlf", "false")
    _run(r, "config", "commit.gpgsign", "false")

    # C0 on main: base file
    (r / "f.txt").write_text("base-1\nbase-2\nbase-3\n", encoding="utf-8")
    _run(r, "add", "f.txt")
    c0 = _commit(r, "base", "2026-01-01T00:00:00+00:00")

    # C1 on feature: the PR — append 5 lines to f.txt, add g.txt(3), h.txt(4) => 12 added
    _run(r, "checkout", "-b", "feature")
    (r / "f.txt").write_text(_F_AFTER_C1, encoding="utf-8")
    (r / "g.txt").write_text("g-1\ng-2\ng-3\n", encoding="utf-8")
    (r / "h.txt").write_text("h-1\nh-2\nh-3\nh-4\n", encoding="utf-8")
    _run(r, "add", "f.txt", "g.txt", "h.txt")
    c1 = _commit(r, "feature work", "2026-01-02T00:00:00+00:00")

    # M on main: --no-ff merge (the merge commit; NEVER a survival SHA)
    _run(r, "checkout", "main")
    _run(r, "-c", "commit.gpgsign=false", "merge", "--no-ff", "-m", "Merge feature", "feature",
         when="2026-01-03T00:00:00+00:00")
    m = _run(r, "rev-parse", "HEAD")

    # C2 on main within the 21d window: rework line-1,2 of f.txt; delete g.txt; h.txt untouched
    (r / "f.txt").write_text(_F_AFTER_C2, encoding="utf-8")
    _run(r, "add", "f.txt")
    _run(r, "rm", "g.txt")
    c2 = _commit(r, "rework two lines and delete g", "2026-01-10T00:00:00+00:00")

    return SimpleNamespace(path=r, c0=c0, c1=c1, m=m, c2=c2)


# ------------------------------ gitio helpers ------------------------------- #
def test_default_branch_is_main(repo):
    assert gitio.default_branch(repo.path) == "main"


def test_is_merge_distinguishes_merge_from_normal(repo):
    assert gitio.is_merge(repo.path, repo.m) is True
    assert gitio.is_merge(repo.path, repo.c1) is False


def test_resolve_rev_is_last_commit_before_window(repo):
    # commits on main before 2026-01-24: C0(1/1), M(1/3), C2(1/10) -> newest is C2
    assert gitio.resolve_rev(repo.path, WINDOW_END, "main") == repo.c2


def test_numstat_counts_added_lines_per_file(repo):
    stats = {path: added for added, _deleted, path in gitio.numstat(repo.path, repo.c1)}
    assert stats == {"f.txt": 5, "g.txt": 3, "h.txt": 4}


def test_blame_deleted_file_empty_surviving_file_full(repo):
    assert gitio.blame_line_shas(repo.path, repo.c2, "g.txt") == []            # deleted -> 0 survivors
    assert gitio.blame_line_shas(repo.path, repo.c2, "h.txt") == [repo.c1] * 4  # untouched -> all C1


# --------------------------- survival core ------------------------ #
def test_survival_before_window_nothing_reworked(repo):
    # blame at the merge itself: no rework has happened yet -> 0 of 12
    assert compute_pr_rework(repo.path, {repo.c1}, repo.m) == (12, 0)


def test_survival_within_window_modified_and_deleted(repo):
    rev = gitio.resolve_rev(repo.path, WINDOW_END, "main")  # == C2
    # f.txt: 2 of 5 reworked; g.txt: 3 of 3 deleted; h.txt: 0 of 4 -> 5 reworked of 12 added
    assert compute_pr_rework(repo.path, {repo.c1}, rev) == (12, 5)


def test_merge_sha_leaking_into_pr_shas_changes_nothing(repo):
    # Survival is the PR's commits, not the merge — the merge is skipped
    # for numstat and is never a blame survivor, so the result is identical.
    rev = gitio.resolve_rev(repo.path, WINDOW_END, "main")
    assert compute_pr_rework(repo.path, {repo.c1, repo.m}, rev) == (12, 5)


def test_missing_object_sha_is_skipped_not_fatal(repo):
    """Squash-merge repos record PR branch SHAs that never enter the canonical
    history — compute_pr_rework must skip them, not crash (integration find)."""
    from tempo import gitio
    from tempo.jobs.rework import compute_pr_rework

    bogus = "d" * 40
    assert not gitio.object_exists(repo.path, bogus)
    assert gitio.object_exists(repo.path, repo.c1)
    with_bogus = compute_pr_rework(repo.path, {repo.c1, bogus}, repo.c2)
    without = compute_pr_rework(repo.path, {repo.c1}, repo.c2)
    assert with_bogus == without
