"""Hand-computed tests for the pure rollup core, jobs.rollup.compute_week.

Covers the cases that live at the rollup grain: a normal n>=3 week with
every metric computed by hand, the n<3 floor masking (value=NULL, real n kept,
uniformly), the empty week, and the no-AI-telemetry slice. The DB wrapper
(run_rollup) stays thin and is not covered here, because it needs a live
Postgres."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tempo.jobs.rollup import compute_week
from tempo.metrics import keys
from tempo.rows import AIUsageRow, PRRow, ReviewRow, ReworkRow

UTC = timezone.utc


def _dt(month: int, day: int, hour: int = 0) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def _pr(pr_id, author, first_commit_at, merged_at, ready_at, is_revert=False) -> PRRow:
    return PRRow(
        id=pr_id, repo="example/repo", number=pr_id, author_hash=author,
        first_commit_at=first_commit_at, ready_at=ready_at, merged_at=merged_at,
        additions=0, deletions=0, changed_files=0, is_revert=is_revert,
    )


def _review(pr_id, reviewer, submitted_at) -> ReviewRow:
    return ReviewRow(id=pr_id * 10 + 1, pr_id=pr_id, reviewer_hash=reviewer,
                     submitted_at=submitted_at, state="APPROVED")


# ---- the normal week fixture (three distinct authors A, B, C -> n = 3) ----- #
def _normal_week():
    prs = [
        _pr(1, "A", _dt(6, 1, 0), _dt(6, 1, 12), _dt(6, 1, 0)),               # lead 12h
        _pr(2, "B", _dt(6, 2, 0), _dt(6, 3, 0), _dt(6, 2, 0)),                # lead 24h
        _pr(3, "C", _dt(6, 3, 0), _dt(6, 4, 12), _dt(6, 3, 0), is_revert=True),  # lead 36h, revert
    ]
    reviews = [
        _review(1, "A", _dt(6, 1, 1)),   # author self-review — excluded
        _review(1, "B", _dt(6, 1, 2)),   # PR1 latency 2h
        _review(2, "A", _dt(6, 2, 6)),   # PR2 latency 6h
        _review(3, "A", _dt(6, 3, 4)),   # PR3 latency 4h
    ]
    rework = [
        ReworkRow(pr_id=1, lines_added=100, lines_reworked_21d=10, sampled=False),
        ReworkRow(pr_id=2, lines_added=100, lines_reworked_21d=40, sampled=False),
    ]
    ai = [
        AIUsageRow(date(2026, 6, 1), "default", "example/repo", 1000, 500, 30.0, 3),
        AIUsageRow(date(2026, 6, 2), "default", "example/repo", 400, 100, 20.0, 2),
    ]
    return prs, reviews, rework, ai


def test_compute_week_covers_exactly_all_metrics():
    prs, reviews, rework, ai = _normal_week()
    result = compute_week(prs, reviews, rework, ai)
    assert set(result) == set(keys.ALL_METRICS)


def test_compute_week_normal_hand_computed():
    prs, reviews, rework, ai = _normal_week()
    result = compute_week(prs, reviews, rework, ai)

    def val(metric):
        return result[metric][0]

    # every metric in this slice carries n = 3
    assert all(n == 3 for _v, n in result.values())

    # lead times [12, 24, 36] -> p50 = 24.0, p90 = 24 + (36-24)*0.8 = 33.6
    assert val(keys.PR_LEAD_TIME_P50) == pytest.approx(24.0)
    assert val(keys.PR_LEAD_TIME_P90) == pytest.approx(33.6)
    # latencies [2, 6, 4] -> sorted [2,4,6] -> p50 = 4.0, p90 = 4 + (6-4)*0.8 = 5.6
    assert val(keys.REVIEW_LATENCY_P50) == pytest.approx(4.0)
    assert val(keys.REVIEW_LATENCY_P90) == pytest.approx(5.6)
    # counts / rates
    assert val(keys.PRS_MERGED) == 3.0
    assert val(keys.REVERT_RATE) == pytest.approx(1 / 3)      # 1 revert / 3 merged
    assert val(keys.REWORK_RATE) == pytest.approx(0.25)       # (10+40)/(100+100)
    # AI: cost 30+20=50, tokens (1000+500)+(400+100)=2000, per PR 50/3
    assert val(keys.AI_COST_USD) == pytest.approx(50.0)
    assert val(keys.AI_TOKENS_TOTAL) == pytest.approx(2000.0)
    assert val(keys.COST_PER_MERGED_PR) == pytest.approx(50 / 3)
    # histogram of [12, 24, 36]: 12->4_24h, 24->1_3d, 36->1_3d
    assert val("pr_lead_time_hist_0_4h") == 0.0
    assert val("pr_lead_time_hist_4_24h") == 1.0
    assert val("pr_lead_time_hist_1_3d") == 2.0
    assert val("pr_lead_time_hist_3_7d") == 0.0
    assert val("pr_lead_time_hist_7_21d") == 0.0
    assert val("pr_lead_time_hist_21d_plus") == 0.0


def test_compute_week_masks_every_metric_below_floor():
    # two authors -> n = 2 < N_FLOOR(3): every value NULL, real n = 2 preserved.
    prs = [
        _pr(1, "A", _dt(6, 1, 0), _dt(6, 1, 12), _dt(6, 1, 0)),
        _pr(2, "B", _dt(6, 2, 0), _dt(6, 3, 0), _dt(6, 2, 0)),
    ]
    reviews = [_review(1, "B", _dt(6, 1, 2))]
    rework = [ReworkRow(pr_id=1, lines_added=50, lines_reworked_21d=5, sampled=False)]
    ai = [AIUsageRow(date(2026, 6, 1), "default", "example/repo", 100, 50, 10.0, 1)]

    result = compute_week(prs, reviews, rework, ai)
    assert set(result) == set(keys.ALL_METRICS)
    assert all(value is None for value, _n in result.values())      # uniformly masked
    assert all(n == 2 for _value, n in result.values())             # ...but real n kept
    # the floor hides even prs_merged and ai_cost, by design
    assert result[keys.PRS_MERGED] == (None, 2)
    assert result[keys.AI_COST_USD] == (None, 2)


def test_compute_week_empty_slice():
    result = compute_week([], [], [], [])
    assert set(result) == set(keys.ALL_METRICS)
    assert all(value is None for value, _n in result.values())
    assert all(n == 0 for _value, n in result.values())


def test_compute_week_no_ai_telemetry_is_none_not_zero():
    prs, reviews, rework, _ai = _normal_week()
    result = compute_week(prs, reviews, rework, [])  # no ai_usage_daily rows for the slice
    # n>=3 so PR metrics still compute...
    assert result[keys.PR_LEAD_TIME_P50] == (pytest.approx(24.0), 3)
    # ...but absent telemetry is missing data, not a real $0
    assert result[keys.AI_COST_USD] == (None, 3)
    assert result[keys.AI_TOKENS_TOTAL] == (None, 3)
    assert result[keys.COST_PER_MERGED_PR] == (None, 3)
