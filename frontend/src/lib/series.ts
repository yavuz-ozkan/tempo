import { N_FLOOR } from "../constants";
import type { MetricPoint, MetricSeries, MetricsResponse } from "../types";
import { formatWeek } from "./format";

/**
 * A chart row: one team-week. Metric values are keyed by their frozen metric
 * name; `null` means the point is masked/absent and Recharts renders a gap
 * (we always draw with connectNulls={false} so masked weeks break the line).
 */
export interface ChartRow {
  week: string; // ISO Monday
  label: string; // "Jun 29"
  n: number; // contributors that week (uniform across metrics per the rollup)
  [metric: string]: number | string | null;
}

export type SeriesIndex = Map<string, Map<string, MetricPoint>>;

/** metric name -> (week -> point), for O(1) lookup while building rows. */
export function indexSeries(resp: MetricsResponse): SeriesIndex {
  const idx: SeriesIndex = new Map();
  for (const s of resp.series) {
    const byWeek = new Map<string, MetricPoint>();
    for (const p of s.points) byWeek.set(p.week, p);
    idx.set(s.metric, byWeek);
  }
  return idx;
}

export function hasSeries(idx: SeriesIndex, metric: string): boolean {
  const m = idx.get(metric);
  return !!m && m.size > 0;
}

/** True if any of the given metrics has at least one non-null value. */
export function hasAnyValue(idx: SeriesIndex, metrics: string[]): boolean {
  for (const metric of metrics) {
    const m = idx.get(metric);
    if (!m) continue;
    for (const p of m.values()) if (p.value !== null) return true;
  }
  return false;
}

/** Sorted union of all weeks appearing across the given metrics. */
function unionWeeks(idx: SeriesIndex, metrics: string[]): string[] {
  const weeks = new Set<string>();
  for (const metric of metrics) {
    const m = idx.get(metric);
    if (!m) continue;
    for (const wk of m.keys()) weeks.add(wk);
  }
  return [...weeks].sort(); // ISO date strings sort chronologically
}

/** n for a week — read from whichever provided metric carries it (they agree).
 * The wire contract allows n to be null; treat that as 0 (masked). */
function nForWeek(idx: SeriesIndex, metrics: string[], week: string): number {
  for (const metric of metrics) {
    const p = idx.get(metric)?.get(week);
    if (p) return p.n ?? 0;
  }
  return 0;
}

/**
 * Build aligned chart rows for a set of metrics across their union of weeks.
 * Masked/missing values become null so lines break rather than dip to zero.
 */
export function buildRows(idx: SeriesIndex, metrics: string[]): ChartRow[] {
  const weeks = unionWeeks(idx, metrics);
  return weeks.map((week) => {
    const row: ChartRow = {
      week,
      label: formatWeek(week),
      n: nForWeek(idx, metrics, week),
    };
    for (const metric of metrics) {
      const p = idx.get(metric)?.get(week);
      row[metric] = p ? p.value : null;
    }
    return row;
  });
}

/**
 * Weeks the rollup masked for insufficient contributors: n < N_FLOOR.
 * Masking is uniform across every metric in a repo-week, so it is computed from
 * n alone. Returned as ISO week strings, chronological.
 */
export function maskedWeeks(idx: SeriesIndex, metrics: string[]): string[] {
  const out: string[] = [];
  for (const week of unionWeeks(idx, metrics)) {
    if (nForWeek(idx, metrics, week) < N_FLOOR) out.push(week);
  }
  return out;
}

/** Human labels for masked weeks, for the badge tooltip. */
export function maskedWeekLabels(weeks: string[]): string[] {
  return weeks.map(formatWeek);
}

/**
 * Sum each histogram bucket's non-null counts across all weeks in range — the
 * lead-time distribution over the visible period ("latest
 * N weeks summed"). Masked weeks contribute nothing.
 */
export function sumHistogram(
  idx: SeriesIndex,
  buckets: readonly { key: string; label: string }[],
): { label: string; count: number; bucketIndex: number }[] {
  return buckets.map((b, i) => {
    let count = 0;
    const m = idx.get(b.key);
    if (m) for (const p of m.values()) if (p.value !== null) count += p.value;
    return { label: b.label, count, bucketIndex: i };
  });
}

/** All series in a response merged into a single value lookup for helpers/tests. */
export function pointsOf(series: MetricSeries[], metric: string): MetricPoint[] {
  return series.find((s) => s.metric === metric)?.points ?? [];
}
