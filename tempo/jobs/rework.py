"""Rework blame job (the "was that AI code actually good?" signal).

Rework rate = % of lines added in a merged PR that are modified or deleted
again within 21 days, measured by git-blame line-survival:

  * lines added per file come from the PR's OWN commits (git show --numstat),
    merge commits and binaries skipped;
  * survival is checked at the tree state ``git rev-list -1 --before=<merged_at
    + 21d> <default branch>`` resolves to — the blame is run at THAT rev;
  * a line "survives" iff blame (-M -C, so renames/moves don't read as rework)
    attributes it to ANY of the PR's commit SHAs from pr_commits — **never the
    merge commit**, which blame never names under a merge-commit workflow;
  * lines_reworked_21d = Σ over files of max(0, added − survived); a file
    deleted by the window's end has zero survivors.

``compute_pr_rework`` is the pure survival core the tests exercise against a
real throwaway repo. ``run_rework`` is the thin DB/clone driver (with the
deterministic sampling fallback). It is not covered by unit tests; running it
needs a live Postgres.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import gitio
from ..config import load_repos
from ..metrics import keys

UTC = timezone.utc

WINDOW = timedelta(days=21)
SAMPLE_THRESHOLD = 30  # repo-week with more eligible PRs than this is sampled
SAMPLE_SIZE = 30


def compute_pr_rework(repo: Path, pr_shas: set[str], rev: str) -> tuple[int, int]:
    """Survival core: return (lines_added, lines_reworked_21d) for one PR.

    ``pr_shas`` is the PR's own commit SHAs (from pr_commits), full 40-hex.
    ``rev`` is the commit to blame at (merged_at + 21d, resolved by the caller).
    Pure over the given repo — no DB, no clock, no network.
    """
    added_per_file: dict[str, int] = {}
    for sha in pr_shas:
        if not gitio.object_exists(repo, sha):
            # Squash-merged PRs: branch commits never enter the canonical
            # history (the squash commit carries the lines instead) — a
            # missing object contributes nothing rather than crashing the job.
            continue
        if gitio.is_merge(repo, sha):  # survival is the PR's commits, not the merge
            continue
        for added, _deleted, path in gitio.numstat(repo, sha):
            if added is None:  # binary file — no lines to count
                continue
            added_per_file[path] = added_per_file.get(path, 0) + added

    total_added = 0
    total_reworked = 0
    for path, added in added_per_file.items():
        if added == 0:
            continue
        blame_shas = gitio.blame_line_shas(repo, rev, path)
        survived = sum(1 for s in blame_shas if s in pr_shas)
        total_added += added
        total_reworked += max(0, added - survived)  # floor 0 per file
    return total_added, total_reworked


def run_rework(*, now: datetime | None = None, conn=None) -> int:
    """Compute rework rows for every merged PR now past its 21-day window that
    has no rework row yet. Returns the number of rows written. Idempotent:
    already-computed PRs are skipped by the anti-join, re-runs upsert.

    Thin wrapper over ``compute_pr_rework`` — the git survival logic is what the
    tests cover; this layer is DB + clone plumbing and is not covered by unit
    tests (it needs a live Postgres).
    """
    now = now or datetime.now(UTC)
    if conn is None:
        from .. import db

        with db.pool().connection() as owned:
            return _run_rework(owned, now)
    return _run_rework(conn, now)


def _run_rework(conn, now: datetime) -> int:
    tracked = {r.slug: r for r in load_repos()}
    cutoff = now - WINDOW

    rows = conn.execute(
        """
        SELECT p.id, p.repo, p.merged_at
          FROM pull_requests p
          LEFT JOIN rework r ON r.pr_id = p.id
         WHERE p.merged_at IS NOT NULL AND p.merged_at <= %s AND r.pr_id IS NULL
        """,
        (cutoff,),
    ).fetchall()

    groups: dict[tuple[str, date], list[tuple[int, datetime]]] = defaultdict(list)
    for pr_id, repo, merged_at in rows:
        groups[(repo, keys.week_of(merged_at))].append((pr_id, merged_at))

    written = 0
    for (repo, week), eligible in groups.items():
        repo_cfg = tracked.get(repo)
        if repo_cfg is None:
            continue  # a merged PR for an untracked repo — nothing to blame against
        gitio.ensure_clone(repo_cfg.git_url, repo_cfg.clone_path)
        branch = gitio.default_branch(repo_cfg.clone_path)

        sampled = False
        if len(eligible) > SAMPLE_THRESHOLD:
            rng = random.Random(f"{repo}:{week.isoformat()}")  # deterministic per repo-week
            eligible = rng.sample(sorted(eligible), SAMPLE_SIZE)
            sampled = True

        for pr_id, merged_at in eligible:
            shas = {
                r[0]
                for r in conn.execute(
                    "SELECT sha FROM pr_commits WHERE pr_id = %s", (pr_id,)
                ).fetchall()
            }
            if not shas:
                continue
            rev = gitio.resolve_rev(repo_cfg.clone_path, merged_at + WINDOW, branch)
            if rev is None:
                continue
            lines_added, lines_reworked = compute_pr_rework(repo_cfg.clone_path, shas, rev)
            conn.execute(
                """
                INSERT INTO rework (pr_id, lines_added, lines_reworked_21d, computed_at, sampled)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (pr_id) DO UPDATE SET
                    lines_added = excluded.lines_added,
                    lines_reworked_21d = excluded.lines_reworked_21d,
                    computed_at = excluded.computed_at,
                    sampled = excluded.sampled
                """,
                (pr_id, lines_added, lines_reworked, now, sampled),
            )
            # Commit per PR: blame runs are long (minutes-to-hours on big
            # repos) and psycopg scopes one transaction to the connection —
            # without this, an interrupted run rolls back EVERYTHING and the
            # job can never finish under repeated interruptions (learned the
            # hard way: two killed runs, zero rows).
            conn.commit()
            written += 1
    return written
