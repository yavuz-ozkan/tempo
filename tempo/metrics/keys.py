"""Frozen metric registry: the exact `metric` strings in
metrics_team_week, the histogram bucket edges, the n-floor, the week
convention, and every chart's limitation footnote (every chart
carries its limitation — the footnotes ship here so nothing else paraphrases them).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

N_FLOOR = 3  # slices backed by fewer distinct contributors are masked (value=NULL)

# --- metric keys (the only values ever written to metrics_team_week.metric) ---
PR_LEAD_TIME_P50 = "pr_lead_time_p50_hours"
PR_LEAD_TIME_P90 = "pr_lead_time_p90_hours"
REVIEW_LATENCY_P50 = "review_latency_p50_hours"
REVIEW_LATENCY_P90 = "review_latency_p90_hours"
PRS_MERGED = "prs_merged"
REVERT_RATE = "revert_rate"
REWORK_RATE = "rework_rate"
AI_COST_USD = "ai_cost_usd"
AI_TOKENS_TOTAL = "ai_tokens_total"
COST_PER_MERGED_PR = "cost_per_merged_pr_usd"

# Lead-time histogram, stored as per-week bucket-count metrics so the API's
# single-table invariant survives (a raw-rows histogram endpoint would not).
# Buckets in hours: [lo, hi); the last is open-ended.
HIST_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("pr_lead_time_hist_0_4h", 0.0, 4.0),
    ("pr_lead_time_hist_4_24h", 4.0, 24.0),
    ("pr_lead_time_hist_1_3d", 24.0, 72.0),
    ("pr_lead_time_hist_3_7d", 72.0, 168.0),
    ("pr_lead_time_hist_7_21d", 168.0, 504.0),
    ("pr_lead_time_hist_21d_plus", 504.0, None),
)

ALL_METRICS: tuple[str, ...] = (
    PR_LEAD_TIME_P50, PR_LEAD_TIME_P90,
    REVIEW_LATENCY_P50, REVIEW_LATENCY_P90,
    PRS_MERGED, REVERT_RATE, REWORK_RATE,
    AI_COST_USD, AI_TOKENS_TOTAL, COST_PER_MERGED_PR,
    *(name for name, _, _ in HIST_BUCKETS),
)

# --- limitation footnotes (rendered on-chart) ---
LIMITATIONS: dict[str, str] = {
    "lead_time": (
        "Start = earliest commit authored-date; rebases rewrite those dates and "
        "long-lived branches inflate the tail. PRs without a datable first commit are excluded from the distribution. p50/p90 shown, never means."
    ),
    "review_latency": (
        "First non-author review only; re-review rounds are ignored, and a "
        "4-minute rubber stamp counts the same as a real review."
    ),
    "revert_rate": (
        "Detects explicit reverts only. A fix-forward culture hides failures "
        "from this metric entirely — it is a floor, not a truth."
    ),
    "rework_rate": (
        "% of added lines modified or deleted again within 21 days. Refactors "
        "and formatting sweeps count as rework; 21 days is a chosen window, "
        "not a law."
    ),
    "ai_spend": (
        "Spend attribution is repo-week level from opt-in resource tags; "
        "unattributed spend makes every per-repo figure a lower bound."
    ),
    "cost_per_pr": (
        "An average over a coarse join (repo-week spend / merged PRs); "
        "unattributed spend makes it a lower bound."
    ),
}

UTC = timezone.utc


def week_of(dt: datetime) -> date:
    """The Monday (UTC) of the ISO week containing `dt` — the team-week grain
    key everywhere. Naive datetimes are rejected: every timestamp in Tempo is
    tz-aware or it doesn't enter the database."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime — Tempo timestamps must be tz-aware")
    d = dt.astimezone(UTC).date()
    return d - timedelta(days=d.weekday())
