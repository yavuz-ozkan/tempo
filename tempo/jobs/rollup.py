"""Nightly rollup into metrics_team_week — THE only served table.

For every (repo, ISO-week of merged_at) with data, compute all the metrics + the
lead-time histogram + prs_merged + the AI metrics, then upsert. The metric
arithmetic is entirely in metrics/formulas.py; this file only fetches rows,
groups them by team-week, and applies the two frozen rollup rules:

  * **n-floor masking:** n = distinct author_hash of the merged PRs
    in the slice; when n < keys.N_FLOOR every metric's value is stored as NULL
    while the real n is kept — uniformly, no exceptions.
  * **AI attribution:** per-repo AI cost/tokens are the weekly sums
    from ai_usage_daily for that repo; the 'unattributed' bucket is rolled up
    under repo='unattributed' (visible, not hidden), its n being the PR-side n
    of that week across ALL repos, or 0.

``compute_week`` is the pure, unit-tested core. ``run_rollup`` is the thin DB
wrapper (no Postgres in unit tests).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime

from ..metrics import formulas, keys
from ..metrics.stats import percentile
from ..rows import AIUsageRow, PRRow, ReviewRow, ReworkRow

# Value is None until proven otherwise; n is shared across a slice's metrics.
MetricResult = dict[str, tuple[float | None, int]]

_AI_METRICS = (keys.AI_COST_USD, keys.AI_TOKENS_TOTAL, keys.COST_PER_MERGED_PR)


def compute_week(
    prs: Sequence[PRRow],
    reviews: Sequence[ReviewRow],
    rework_rows: Sequence[ReworkRow],
    ai_rows: Sequence[AIUsageRow],
) -> MetricResult:
    """Pure metric core for one (repo, week) slice.

    Returns { metric_name: (value_or_None, n) } covering EXACTLY keys.ALL_METRICS.
    ``prs`` are the merged PRs of the slice; ``reviews``/``rework_rows`` belong
    to them; ``ai_rows`` are the ai_usage_daily rows for the slice. n is the
    distinct author_hash count of the merged PRs; when n < keys.N_FLOOR every
    value is masked to None while n is preserved.
    """
    merged = [p for p in prs if p.merged_at is not None]
    n = len({p.author_hash for p in merged})

    lead_times = formulas.pr_lead_time_hours(merged)
    latencies = formulas.review_latency_hours(merged, reviews)

    raw: dict[str, float | None] = {}
    raw[keys.PR_LEAD_TIME_P50] = percentile(lead_times, 50) if lead_times else None
    raw[keys.PR_LEAD_TIME_P90] = percentile(lead_times, 90) if lead_times else None
    raw[keys.REVIEW_LATENCY_P50] = percentile(latencies, 50) if latencies else None
    raw[keys.REVIEW_LATENCY_P90] = percentile(latencies, 90) if latencies else None
    raw[keys.PRS_MERGED] = float(len(merged))
    raw[keys.REVERT_RATE] = formulas.revert_rate(merged)
    raw[keys.REWORK_RATE] = formulas.rework_rate(rework_rows)

    # AI metrics: absent telemetry is missing data, not a real $0 (unattributed
    # spend makes per-repo a lower bound). So no ai_rows ⇒ None.
    if ai_rows:
        ai_cost = float(sum(r.cost_usd for r in ai_rows))
        ai_tokens = float(sum(r.tokens_in + r.tokens_out for r in ai_rows))
        raw[keys.AI_COST_USD] = ai_cost
        raw[keys.AI_TOKENS_TOTAL] = ai_tokens
        raw[keys.COST_PER_MERGED_PR] = formulas.cost_per_merged_pr(ai_cost, len(merged))
    else:
        raw[keys.AI_COST_USD] = None
        raw[keys.AI_TOKENS_TOTAL] = None
        raw[keys.COST_PER_MERGED_PR] = None

    for name, count in formulas.lead_time_histogram(lead_times).items():
        raw[name] = float(count)

    masked = n < keys.N_FLOOR
    return {metric: (None if masked else value, n) for metric, value in raw.items()}


def run_rollup(*, conn=None) -> int:
    """Recompute metrics_team_week from the raw tables. Returns rows upserted.
    Idempotent (upsert keyed on (week, repo, metric)). Thin DB wrapper — the
    coverage lives in ``compute_week``."""
    if conn is None:
        from .. import db

        with db.pool().connection() as owned:
            return _run_rollup(owned)
    return _run_rollup(conn)


def _run_rollup(conn) -> int:
    prs = [
        PRRow(*r)
        for r in conn.execute(
            "SELECT id, repo, number, author_hash, first_commit_at, ready_at, "
            "merged_at, additions, deletions, changed_files, is_revert "
            "FROM pull_requests WHERE merged_at IS NOT NULL"
        ).fetchall()
    ]
    reviews = [
        ReviewRow(*r)
        for r in conn.execute(
            "SELECT id, pr_id, reviewer_hash, submitted_at, state FROM reviews"
        ).fetchall()
    ]
    rework_rows = [
        ReworkRow(*r)
        for r in conn.execute(
            "SELECT pr_id, lines_added, lines_reworked_21d, sampled FROM rework"
        ).fetchall()
    ]
    ai_rows = [
        AIUsageRow(day, org_unit, repo, tokens_in, tokens_out, float(cost_usd), sessions)
        for (day, org_unit, repo, tokens_in, tokens_out, cost_usd, sessions) in conn.execute(
            "SELECT day, org_unit, repo, tokens_in, tokens_out, cost_usd, sessions "
            "FROM ai_usage_daily"
        ).fetchall()
    ]

    reviews_by_pr: dict[int, list[ReviewRow]] = defaultdict(list)
    for rv in reviews:
        reviews_by_pr[rv.pr_id].append(rv)
    rework_by_pr = {rw.pr_id: rw for rw in rework_rows}

    pr_groups: dict[tuple[str, date], list[PRRow]] = defaultdict(list)
    merged_by_week: dict[date, list[PRRow]] = defaultdict(list)
    for p in prs:
        week = keys.week_of(p.merged_at)
        pr_groups[(p.repo, week)].append(p)
        merged_by_week[week].append(p)

    ai_groups: dict[tuple[str, date], list[AIUsageRow]] = defaultdict(list)
    for a in ai_rows:
        week = keys.week_of(datetime(a.day.year, a.day.month, a.day.day, tzinfo=keys.UTC))
        ai_groups[(a.repo, week)].append(a)

    written = 0
    for (repo, week), group in pr_groups.items():
        slice_reviews = [rv for p in group for rv in reviews_by_pr.get(p.id, ())]
        slice_rework = [rework_by_pr[p.id] for p in group if p.id in rework_by_pr]
        result = compute_week(group, slice_reviews, slice_rework, ai_groups.get((repo, week), []))
        written += _upsert(conn, week, repo, result, result.keys())

    # Unattributed AI spend: emit only the AI metrics, n = cross-repo PR-side n.
    for (repo, week), group in ai_groups.items():
        if repo != "unattributed":
            continue
        result = compute_week(merged_by_week.get(week, []), (), (), group)
        written += _upsert(conn, week, "unattributed", result, _AI_METRICS)

    return written


def _upsert(conn, week: date, repo: str, result: MetricResult, metrics) -> int:
    count = 0
    for metric in metrics:
        value, n = result[metric]
        conn.execute(
            """
            INSERT INTO metrics_team_week (week, repo, metric, value, n)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (week, repo, metric) DO UPDATE SET
                value = excluded.value, n = excluded.n
            """,
            (week, repo, metric, value, n),
        )
        count += 1
    return count
