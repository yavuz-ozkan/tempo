import {
  AI_COST_USD,
  AI_TOKENS_TOTAL,
  COST_PER_MERGED_PR,
  HIST_BUCKETS,
  PR_LEAD_TIME_P50,
  PR_LEAD_TIME_P90,
  PRS_MERGED,
  REVERT_RATE,
  REVIEW_LATENCY_P50,
  REVIEW_LATENCY_P90,
  REWORK_RATE,
} from "../constants";
import type {
  MetricSeries,
  MetricsQuery,
  MetricsResponse,
  TempoApi,
} from "../types";

// -----------------------------------------------------------------------------
// MOCK dataset — VITE_MOCK=1 renders all five views with no backend.
//
// One repo, 26 team-weeks, realistic magnitudes:
//   lead time p50 ~10-40h, rework 5-15%, weekly AI cost $20-200.
// A gentle narrative is baked in — the story the centerpiece chart tells:
// AI adoption rises, lead time drifts down, PR volume up, review latency up
// (review becomes the bottleneck). Two weeks are masked (n=2 < N_FLOOR) so the
// "insufficient n" gap + badge path is exercised.
//
// Everything is deterministic given the week index, so the fixture is stable
// across reloads (only the week dates track "today").
// -----------------------------------------------------------------------------

const MOCK_REPO = "example/repo";
const WEEK_COUNT = 26;
const MASKED_WEEKS = new Set<number>([6, 17]); // n < N_FLOOR -> all metrics null

// Deterministic PRNG (mulberry32) so magnitudes never jitter between renders.
function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The last `count` Mondays (UTC), ascending, ending on/just before today. */
function recentMondays(count: number): string[] {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun..6=Sat
  const backToMonday = (day + 6) % 7; // days since Monday
  const lastMonday = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - backToMonday),
  );
  const out: string[] = [];
  for (let i = count - 1; i >= 0; i--) {
    const d = new Date(lastMonday);
    d.setUTCDate(d.getUTCDate() - i * 7);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

interface WeekFacts {
  n: number;
  masked: boolean;
  values: Record<string, number>;
}

/** Compute one week's facts. `t` is 0..1 progress across the 26-week window. */
function weekFacts(i: number, t: number): WeekFacts {
  const r = rng((i + 1) * 2654435761);
  const masked = MASKED_WEEKS.has(i);

  // Volume climbs as agents compress the easy middle of tasks.
  const prsMerged = Math.round(clamp(7 + t * 14 + (r() - 0.5) * 6, 4, 30));

  // Lead time drifts down (10-40h band), heavy-tailed p90.
  const leadP50 = clamp(36 - t * 20 + (r() - 0.5) * 8, 10, 40);
  const leadP90 = clamp(leadP50 * (2.4 + r() * 1.6), leadP50 + 6, 220);

  // Review latency drifts UP — the bottleneck moves to review.
  const reviewP50 = clamp(3 + t * 12 + (r() - 0.5) * 4, 1.5, 20);
  const reviewP90 = clamp(reviewP50 * (2.1 + r() * 1.8), reviewP50 + 3, 90);

  const reworkRate = clamp(0.07 + (r() - 0.4) * 0.09, 0.05, 0.15); // 5-15%
  const revertRate = clamp(r() * 0.07, 0, 0.08);

  const aiCost = clamp(22 + t * 150 + (r() - 0.5) * 40, 20, 200); // $20-200/wk
  const aiTokens = Math.round(aiCost * (180000 + r() * 120000)); // plausible
  const costPerPr = aiCost / prsMerged;

  // Distribute merged PRs across the six lead-time buckets. Faster weeks pack
  // the low buckets; the shape shifts left over time. Sum is reconciled to
  // prsMerged so the histogram totals are honest.
  const shape = [
    0.10 + t * 0.22,
    0.20 + t * 0.12,
    0.26 - t * 0.04,
    0.22 - t * 0.12,
    0.14 - t * 0.10,
    0.08 - t * 0.06,
  ].map((w) => Math.max(0.01, w));
  const shapeSum = shape.reduce((a, b) => a + b, 0);
  const rawCounts = shape.map((w) => (w / shapeSum) * prsMerged);
  const counts = rawCounts.map((c) => Math.floor(c));
  let remainder = prsMerged - counts.reduce((a, b) => a + b, 0);
  // Hand the leftover PRs to the buckets with the largest fractional parts.
  const order = rawCounts
    .map((c, idx) => ({ idx, frac: c - Math.floor(c) }))
    .sort((a, b) => b.frac - a.frac);
  for (let k = 0; k < order.length && remainder > 0; k++, remainder--) {
    const entry = order[k];
    if (entry) counts[entry.idx] = (counts[entry.idx] ?? 0) + 1;
  }

  const values: Record<string, number> = {
    [PR_LEAD_TIME_P50]: round1(leadP50),
    [PR_LEAD_TIME_P90]: round1(leadP90),
    [REVIEW_LATENCY_P50]: round1(reviewP50),
    [REVIEW_LATENCY_P90]: round1(reviewP90),
    [PRS_MERGED]: prsMerged,
    [REVERT_RATE]: round3(revertRate),
    [REWORK_RATE]: round3(reworkRate),
    [AI_COST_USD]: round2(aiCost),
    [AI_TOKENS_TOTAL]: aiTokens,
    [COST_PER_MERGED_PR]: round2(costPerPr),
  };
  HIST_BUCKETS.forEach((b, idx) => {
    values[b.key] = counts[idx] ?? 0;
  });

  // n is the distinct-contributor count behind the slice; masked weeks sit
  // below the floor. Non-masked weeks scale a little with volume.
  const n = masked ? 2 : clamp(3 + Math.round(r() * 5), 3, 9);
  return { n, masked, values };
}

const round1 = (v: number) => Math.round(v * 10) / 10;
const round2 = (v: number) => Math.round(v * 100) / 100;
const round3 = (v: number) => Math.round(v * 1000) / 1000;

function buildResponse(): MetricsResponse {
  const weeks = recentMondays(WEEK_COUNT);
  const facts = weeks.map((_, i) => weekFacts(i, WEEK_COUNT > 1 ? i / (WEEK_COUNT - 1) : 0));

  const metricKeys = [
    PR_LEAD_TIME_P50,
    PR_LEAD_TIME_P90,
    REVIEW_LATENCY_P50,
    REVIEW_LATENCY_P90,
    PRS_MERGED,
    REVERT_RATE,
    REWORK_RATE,
    AI_COST_USD,
    AI_TOKENS_TOTAL,
    COST_PER_MERGED_PR,
    ...HIST_BUCKETS.map((b) => b.key),
  ];

  const series: MetricSeries[] = metricKeys.map((metric) => ({
    metric,
    points: weeks.map((week, i) => {
      const f = facts[i]!;
      return {
        week,
        // Masked weeks: value=null uniformly, real n retained.
        value: f.masked ? null : (f.values[metric] ?? null),
        n: f.n,
      };
    }),
  }));

  return { repo: MOCK_REPO, series };
}

const CACHED = buildResponse();

function filterByRange(resp: MetricsResponse, query: MetricsQuery): MetricsResponse {
  const wantMetrics =
    query.metrics && query.metrics.length > 0 ? new Set(query.metrics) : null;
  const from = query.from ?? null;
  const to = query.to ?? null;
  return {
    repo: resp.repo,
    series: resp.series
      .filter((s) => (wantMetrics ? wantMetrics.has(s.metric) : true))
      .map((s) => ({
        metric: s.metric,
        points: s.points.filter(
          (p) => (!from || p.week >= from) && (!to || p.week <= to),
        ),
      })),
  };
}

export const mockApi: TempoApi = {
  async getRepos(): Promise<string[]> {
    return [MOCK_REPO];
  },
  async getMetrics(query: MetricsQuery): Promise<MetricsResponse> {
    return filterByRange(CACHED, query);
  },
};
