/**
 * Wire-contract types — a 1:1 mirror of the API serving layer
 * (api/serve.py). Both the real fetch layer and the
 * mock implement `TempoApi`, so the UI is identical against either.
 *
 *   GET /api/repos   -> { "repos": [slug, ...] }
 *   GET /api/metrics?repo=&metrics=&from=&to= ->
 *     { "repo": "example/repo",
 *       "series": [{ "metric": "pr_lead_time_p50_hours",
 *                    "points": [{ "week": "2026-06-29", "value": 12.4, "n": 5 },
 *                               { "week": "2026-07-06", "value": null, "n": 2 }] }] }
 *
 * Weeks ascending. `value: null` = a slice the rollup masked because n < N_FLOOR
 * — rendered as "insufficient n", never as zero.
 */

export interface MetricPoint {
  /** ISO date of the Monday (UTC) of the team-week. */
  week: string;
  /** null = masked (n < N_FLOOR) or not computable that week. */
  value: number | null;
  /** distinct contributors backing the slice; stored even when value is null. */
  n: number | null;
}

export interface MetricSeries {
  metric: string;
  points: MetricPoint[];
}

export interface MetricsResponse {
  repo: string;
  series: MetricSeries[];
}

export interface ReposResponse {
  repos: string[];
}

export interface MetricsQuery {
  repo: string;
  from?: string;
  to?: string;
  metrics?: readonly string[];
}

/** The single interface the real client and the mock both satisfy. */
export interface TempoApi {
  getRepos(): Promise<string[]>;
  getMetrics(query: MetricsQuery): Promise<MetricsResponse>;
}
