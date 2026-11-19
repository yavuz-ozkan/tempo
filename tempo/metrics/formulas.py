"""Metric formulas — THE PRODUCT.

Every metric is a *pure function over `rows.py` dataclasses*. No DB imports, no
network, no clock: the rollup job (jobs/rollup.py) fetches rows and calls these;
tests feed hand-built rows and assert hand-computed numbers. If a formula
changes, a test must change — that is the point.

Design invariants pinned by the frozen signatures:
  * per-PR value lists SKIP the PRs they cannot honestly measure — they never
    emit a sentinel, a zero, or a negative;
  * rates return None (not 0.0) when their denominator is empty, so the rollup
    can tell "genuinely zero" from "no data to divide".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..rows import PRRow, ReviewRow, ReworkRow
from .keys import HIST_BUCKETS

_SECONDS_PER_HOUR = 3600.0


def pr_lead_time_hours(prs: Sequence[PRRow]) -> list[float]:
    """Lead time = merged_at − first_commit_at, in hours, one value per PR.

    Skips (never emits a value for) a PR that is missing either timestamp, and
    skips the rebase-weirdness case where first_commit_at is AFTER merged_at —
    that is a rewritten authored-date, not a negative lead time.
    Equal timestamps yield a legitimate 0.0.
    """
    out: list[float] = []
    for pr in prs:
        start = pr.first_commit_at
        end = pr.merged_at
        if start is None or end is None:
            continue
        if start > end:  # rebase rewrote the authored date past the merge — skip, never negative
            continue
        out.append((end - start).total_seconds() / _SECONDS_PER_HOUR)
    return out


def review_latency_hours(
    prs: Sequence[PRRow], reviews: Sequence[ReviewRow]
) -> list[float]:
    """Review latency = (first non-author review submitted strictly after
    ready_at) − ready_at, in hours, one value per PR.

    Non-author = reviewer_hash != author_hash (the whole reason the hash
    exists). Reviews at or before ready_at do not count; a PR with no qualifying
    review is skipped (this is what makes a self-review-only PR contribute
    nothing). A PR without a ready_at is skipped.
    """
    by_pr: dict[int, list[ReviewRow]] = defaultdict(list)
    for rv in reviews:
        by_pr[rv.pr_id].append(rv)

    out: list[float] = []
    for pr in prs:
        ready = pr.ready_at
        if ready is None:
            continue
        qualifying = [
            rv.submitted_at
            for rv in by_pr.get(pr.id, ())
            if rv.reviewer_hash != pr.author_hash and rv.submitted_at > ready
        ]
        if not qualifying:
            continue
        first = min(qualifying)
        out.append((first - ready).total_seconds() / _SECONDS_PER_HOUR)
    return out


def revert_rate(prs: Sequence[PRRow]) -> float | None:
    """Reverting merged PRs ÷ merged PRs. Only merged PRs count on
    either side. None when the week has no merged PRs (nothing to divide)."""
    merged = [pr for pr in prs if pr.merged_at is not None]
    if not merged:
        return None
    reverts = sum(1 for pr in merged if pr.is_revert)
    return reverts / len(merged)


def rework_rate(rework_rows: Sequence[ReworkRow]) -> float | None:
    """Σ(lines_reworked_21d) ÷ Σ(lines_added) across the week's PRs.
    None when no lines were added (nothing to divide)."""
    added = sum(r.lines_added for r in rework_rows)
    if added == 0:
        return None
    reworked = sum(r.lines_reworked_21d for r in rework_rows)
    return reworked / added


def lead_time_histogram(lead_times: Sequence[float]) -> dict[str, int]:
    """Bucket lead-time hours into the frozen keys.HIST_BUCKETS edges.

    Buckets are [lo, hi); the final bucket is open-ended (hi is None). Every
    bucket name is present in the result, zero-filled — the rollup stores one
    count metric per bucket so the single-served-table invariant survives.
    """
    counts: dict[str, int] = {name: 0 for name, _, _ in HIST_BUCKETS}
    for value in lead_times:
        for name, lo, hi in HIST_BUCKETS:
            if value >= lo and (hi is None or value < hi):
                counts[name] += 1
                break
    return counts


def cost_per_merged_pr(ai_cost_usd: float, merged_pr_count: int) -> float | None:
    """Repo-week AI spend ÷ merged PRs that repo-week. None when no
    PRs merged (nothing to divide)."""
    if merged_pr_count == 0:
        return None
    return ai_cost_usd / merged_pr_count
