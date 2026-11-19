"""Frozen row contracts. Metric formulas consume EXACTLY
these shapes (each metric is a pure function `rows -> value`), and
collectors/jobs produce them. Field names mirror schema.sql columns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class PRRow:
    id: int
    repo: str
    number: int
    author_hash: str
    first_commit_at: datetime | None
    ready_at: datetime | None
    merged_at: datetime | None
    additions: int
    deletions: int
    changed_files: int
    is_revert: bool


@dataclass(frozen=True, slots=True)
class ReviewRow:
    id: int
    pr_id: int
    reviewer_hash: str
    submitted_at: datetime
    state: str


@dataclass(frozen=True, slots=True)
class ReworkRow:
    pr_id: int
    lines_added: int
    lines_reworked_21d: int
    sampled: bool


@dataclass(frozen=True, slots=True)
class AIUsageRow:
    day: date
    org_unit: str
    repo: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    sessions: int
