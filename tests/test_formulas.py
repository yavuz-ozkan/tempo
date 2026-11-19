"""Hand-computed tests for the metric formulas (fixture list: normal,
draft, rebase-weirdness, revert, empty week, self-review-only). Every expected
number here is computed by hand in the comments — if a formula changes, one of
these must change."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tempo.metrics import formulas
from tempo.metrics.keys import HIST_BUCKETS
from tempo.rows import PRRow, ReviewRow, ReworkRow

UTC = timezone.utc


def _dt(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _pr(
    pr_id: int,
    *,
    author: str = "A",
    first_commit_at=None,
    ready_at=None,
    merged_at=None,
    is_revert: bool = False,
) -> PRRow:
    return PRRow(
        id=pr_id,
        repo="example/repo",
        number=pr_id,
        author_hash=author,
        first_commit_at=first_commit_at,
        ready_at=ready_at,
        merged_at=merged_at,
        additions=0,
        deletions=0,
        changed_files=0,
        is_revert=is_revert,
    )


def _review(pr_id: int, reviewer: str, submitted_at: datetime, state: str = "APPROVED") -> ReviewRow:
    return ReviewRow(id=pr_id * 100 + hash(reviewer) % 97, pr_id=pr_id, reviewer_hash=reviewer,
                     submitted_at=submitted_at, state=state)


# --------------------------------------------------------------------------- #
# pr_lead_time_hours
# --------------------------------------------------------------------------- #
def test_pr_lead_time_normal_skips_missing_and_rebase():
    prs = [
        _pr(1, first_commit_at=_dt(6, 1, 0), merged_at=_dt(6, 1, 12)),   # 12.0h
        _pr(2, first_commit_at=_dt(6, 2, 0), merged_at=_dt(6, 5, 0)),    # 72.0h (3 days)
        _pr(3, first_commit_at=None, merged_at=_dt(6, 1, 12)),           # skip: no first_commit
        _pr(4, first_commit_at=_dt(6, 1, 0), merged_at=None),            # skip: not merged
        _pr(5, first_commit_at=_dt(6, 10, 0), merged_at=_dt(6, 9, 0)),   # rebase-weird: skip, NOT -24
    ]
    assert formulas.pr_lead_time_hours(prs) == [12.0, 72.0]


def test_pr_lead_time_equal_timestamps_is_zero_not_skipped():
    prs = [_pr(1, first_commit_at=_dt(6, 1, 0), merged_at=_dt(6, 1, 0))]
    assert formulas.pr_lead_time_hours(prs) == [0.0]


def test_pr_lead_time_fractional_hours():
    prs = [_pr(1, first_commit_at=_dt(6, 1, 0, 0), merged_at=_dt(6, 1, 1, 30))]  # 1h30m = 1.5h
    assert formulas.pr_lead_time_hours(prs) == [1.5]


def test_pr_lead_time_empty_week():
    assert formulas.pr_lead_time_hours([]) == []


# --------------------------------------------------------------------------- #
# review_latency_hours
# --------------------------------------------------------------------------- #
def test_review_latency_first_nonauthor_after_ready():
    prs = [
        _pr(1, author="A", ready_at=_dt(6, 1, 0)),   # non-author B at 02:00 -> 2.0h
        _pr(2, author="B", ready_at=_dt(6, 2, 0)),   # self-review only -> skipped
        _pr(3, author="C", ready_at=_dt(6, 3, 5)),   # review before ready excluded; Y 06:30 -> 1.5h
    ]
    reviews = [
        _review(1, "A", _dt(6, 1, 1)),    # author self-review — excluded
        _review(1, "B", _dt(6, 1, 2)),    # first non-author -> 2.0h
        _review(1, "C", _dt(6, 1, 3)),    # later non-author — ignored (first wins)
        _review(2, "B", _dt(6, 2, 1)),    # PR2 author self-review — excluded -> PR2 skipped
        _review(3, "X", _dt(6, 3, 4)),    # before ready_at 05:00 — excluded
        _review(3, "Y", _dt(6, 3, 6, 30)),  # 06:30 - 05:00 = 1.5h
    ]
    assert formulas.review_latency_hours(prs, reviews) == [2.0, 1.5]


def test_review_latency_at_ready_is_excluded_strictly_after():
    prs = [_pr(1, author="A", ready_at=_dt(6, 1, 0))]
    reviews = [_review(1, "B", _dt(6, 1, 0))]  # submitted_at == ready_at -> NOT strictly after
    assert formulas.review_latency_hours(prs, reviews) == []


def test_review_latency_no_ready_at_skipped():
    prs = [_pr(1, author="A", ready_at=None)]
    reviews = [_review(1, "B", _dt(6, 1, 5))]
    assert formulas.review_latency_hours(prs, reviews) == []


def test_review_latency_empty():
    assert formulas.review_latency_hours([], []) == []


# --------------------------------------------------------------------------- #
# revert_rate
# --------------------------------------------------------------------------- #
def test_revert_rate_only_counts_merged():
    prs = [
        _pr(1, merged_at=_dt(6, 1), is_revert=True),   # merged revert
        _pr(2, merged_at=_dt(6, 1)),                   # merged
        _pr(3, merged_at=_dt(6, 1)),                   # merged
        _pr(4, merged_at=_dt(6, 1)),                   # merged  -> 4 merged, 1 revert
        _pr(5, merged_at=None, is_revert=True),        # unmerged revert — NOT counted
    ]
    assert formulas.revert_rate(prs) == 0.25  # 1 / 4


def test_revert_rate_none_when_no_merged():
    prs = [_pr(1, merged_at=None, is_revert=True), _pr(2, merged_at=None)]
    assert formulas.revert_rate(prs) is None
    assert formulas.revert_rate([]) is None


# --------------------------------------------------------------------------- #
# rework_rate
# --------------------------------------------------------------------------- #
def test_rework_rate_sum_over_sum():
    rows = [
        ReworkRow(pr_id=1, lines_added=100, lines_reworked_21d=10, sampled=False),
        ReworkRow(pr_id=2, lines_added=100, lines_reworked_21d=40, sampled=False),
    ]
    assert formulas.rework_rate(rows) == 0.25  # (10 + 40) / (100 + 100)


def test_rework_rate_none_when_no_lines_added():
    rows = [ReworkRow(pr_id=1, lines_added=0, lines_reworked_21d=0, sampled=False)]
    assert formulas.rework_rate(rows) is None
    assert formulas.rework_rate([]) is None


# --------------------------------------------------------------------------- #
# lead_time_histogram
# --------------------------------------------------------------------------- #
def test_lead_time_histogram_buckets_and_boundaries():
    # buckets (hours): 0_4h[0,4) 4_24h[4,24) 1_3d[24,72) 3_7d[72,168) 7_21d[168,504) 21d_plus[504,inf)
    lead_times = [1.0, 3.9, 4.0, 12.0, 24.0, 72.0, 200.0, 600.0]
    #  1.0->0_4h  3.9->0_4h  4.0->4_24h  12.0->4_24h  24.0->1_3d  72.0->3_7d  200.0->7_21d  600.0->21d_plus
    assert formulas.lead_time_histogram(lead_times) == {
        "pr_lead_time_hist_0_4h": 2,
        "pr_lead_time_hist_4_24h": 2,
        "pr_lead_time_hist_1_3d": 1,
        "pr_lead_time_hist_3_7d": 1,
        "pr_lead_time_hist_7_21d": 1,
        "pr_lead_time_hist_21d_plus": 1,
    }


def test_lead_time_histogram_empty_all_zero():
    result = formulas.lead_time_histogram([])
    assert result == {name: 0 for name, _, _ in HIST_BUCKETS}
    assert sum(result.values()) == 0


# --------------------------------------------------------------------------- #
# cost_per_merged_pr
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cost, count, expected",
    [
        (100.0, 8, 12.5),   # 100 / 8
        (0.0, 5, 0.0),      # genuinely zero spend over 5 PRs
        (100.0, 0, None),   # nothing to divide by
    ],
)
def test_cost_per_merged_pr(cost, count, expected):
    assert formulas.cost_per_merged_pr(cost, count) == expected
