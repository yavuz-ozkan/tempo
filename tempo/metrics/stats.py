"""Frozen statistics core. One percentile definition for the
whole codebase: distributions, p50/p90, never means."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def percentile(values: Sequence[float], p: float) -> float:
    """Linear interpolation between closest ranks (numpy's default 'linear'
    method). `p` in [0, 100]. Raises ValueError on an empty sequence — callers
    decide what an empty week means; this function never guesses."""
    if not values:
        raise ValueError("percentile() of empty sequence")
    if not 0 <= p <= 100:
        raise ValueError(f"p must be in [0, 100], got {p}")
    xs = sorted(values)
    rank = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(xs[lo])
    return float(xs[lo] + (xs[hi] - xs[lo]) * (rank - lo))


@dataclass(frozen=True, slots=True)
class Percentiles:
    p50: float
    p90: float
    count: int


def percentiles(values: Sequence[float]) -> Percentiles:
    return Percentiles(p50=percentile(values, 50), p90=percentile(values, 90), count=len(values))
