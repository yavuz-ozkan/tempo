"""Git plumbing for the rework blame job.

Every helper follows the same *subprocess conventions*:
every git call is a list of args (never
``shell=True``), stdout is captured as BYTES and decoded UTF-8 with
``errors="replace"`` LATE, and ``-c core.quotepath=false`` is passed so paths
with non-ASCII bytes are not octal-escaped.

These helpers operate on any local repo path (a bare clone in production, a
throwaway working repo under pytest tmp_path in the tests). No helper here
touches the network except ``ensure_clone``.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc

# numstat line: "<added>\t<deleted>\t<path>", where added/deleted are "-" for binaries.
_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$")
# --line-porcelain header: "<40-hex> <orig-lineno> <final-lineno>[ <group-size>]".
_PORCELAIN_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")
# rename path forms git emits in numstat: "old => new" and "pre/{old => new}/post".
_BRACE_RENAME_RE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


class GitError(RuntimeError):
    """A git subprocess exited non-zero when the caller required success."""


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    """Run ``git -C <repo> -c core.quotepath=false <args>`` and return raw
    stdout bytes. Never uses a shell. Decode is the caller's job, done late."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _resolve_rename(path: str) -> str:
    """Return the post-rename path for a numstat path token. git writes renames
    as ``old => new`` or ``pre/{old => new}/post``; blame -M -C is what actually
    follows the content, so here we only need the current (new) path to blame."""
    m = _BRACE_RENAME_RE.match(path)
    if m:
        pre, _old, new, post = m.groups()
        return f"{pre}{new}{post}"
    if " => " in path:
        return path.split(" => ", 1)[1]
    return path


def ensure_clone(git_url: str, dest: Path) -> None:
    """Bare-clone ``git_url`` into ``dest`` if absent, else fetch+prune.

    The ONLY network call in this module. The rework job keeps one bare clone
    per tracked repo (config/repos.yaml clone_path); blame runs against it.
    """
    dest = Path(dest)
    if (dest / "HEAD").exists():
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--prune", "origin"],
            capture_output=True,
            check=True,
        )
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--bare", git_url, str(dest)],
        capture_output=True,
        check=True,
    )


def default_branch(repo: Path) -> str:
    """The repo's HEAD branch name (a bare clone's HEAD points at the remote's
    default branch). Falls back to main/master by existence probe."""
    raw = _git(repo, "symbolic-ref", "--short", "HEAD", check=False)
    branch = _decode(raw).strip()
    if branch:
        return branch
    for candidate in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", candidate, check=False).strip():
            return candidate
    return "main"


def resolve_rev(repo: Path, before: datetime, branch: str) -> str | None:
    """``git rev-list -1 --before=<before> <branch>`` — the newest commit on
    ``branch`` dated before ``before`` (blame at merged_at + 21d).
    Returns None when no such commit exists (branch younger than the window)."""
    iso = before.astimezone(UTC).isoformat()
    raw = _git(repo, "rev-list", "-1", f"--before={iso}", branch, check=False)
    sha = _decode(raw).strip()
    return sha or None


def object_exists(repo: Path, sha: str) -> bool:
    """True when `sha` resolves to a commit in this clone. Squash/rebase merges
    leave PR branch commits absent from the canonical history, so pr_commits
    rows are not guaranteed to exist here."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
    )
    return proc.returncode == 0


def parents(repo: Path, sha: str) -> list[str]:
    """Parent SHAs of a commit (2+ ⇒ merge commit)."""
    raw = _git(repo, "show", "-s", "--format=%P", sha)
    return _decode(raw).split()


def is_merge(repo: Path, sha: str) -> bool:
    """A merge commit has >1 parent; numstat on it is skipped."""
    return len(parents(repo, sha)) > 1


def numstat(repo: Path, sha: str) -> list[tuple[int | None, int | None, str]]:
    """``git show --numstat`` for one commit → [(added, deleted, path), ...].

    Rename/copy detection on (``-M -C``); binary files come back with added and
    deleted set to None (their "-\t-" line). Path is resolved to its post-rename
    form. Merge commits are the caller's responsibility to skip.
    """
    raw = _git(repo, "show", "--numstat", "--format=", "-M", "-C", sha)
    out: list[tuple[int | None, int | None, str]] = []
    for line in _decode(raw).splitlines():
        m = _NUMSTAT_RE.match(line)
        if not m:
            continue
        added_s, deleted_s, path = m.groups()
        added = None if added_s == "-" else int(added_s)
        deleted = None if deleted_s == "-" else int(deleted_s)
        out.append((added, deleted, _resolve_rename(path)))
    return out


def blame_line_shas(repo: Path, rev: str, path: str) -> list[str]:
    """``git blame --line-porcelain -M -C <rev> -- <path>`` → the authoring SHA
    of every line in the file at ``rev``, in file order.

    -M -C follows moved/copied code so renames don't read as 100% rework.
    If the path does not exist at ``rev`` (deleted before the window
    closes), git exits non-zero and this returns [] — i.e. zero survivors, the
    defined behavior for a deleted file.
    """
    raw = _git(repo, "blame", "--line-porcelain", "-M", "-C", rev, "--", path, check=False)
    shas: list[str] = []
    current: str | None = None
    for line in _decode(raw).splitlines():
        m = _PORCELAIN_HEADER_RE.match(line)
        if m:
            current = m.group(1)
        elif line.startswith("\t") and current is not None:
            shas.append(current)
    return shas
