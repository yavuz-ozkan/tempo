/**
 * FROZEN COPY — reproduced verbatim from tempo/metrics/keys.py.
 *
 * The metric-name strings, histogram bucket names, the n-floor, and every
 * LIMITATION footnote are frozen copy (every chart
 * carries its limitation; the footnotes ship in keys.py so nothing else paraphrases
 * them). If keys.py changes, this file changes — it is the only allowed source
 * of these strings on the frontend.
 */

// Slices backed by fewer distinct contributors are masked (value=NULL).
export const N_FLOOR = 3;

// --- metric keys (the only values ever written to metrics_team_week.metric) ---
export const PR_LEAD_TIME_P50 = "pr_lead_time_p50_hours";
export const PR_LEAD_TIME_P90 = "pr_lead_time_p90_hours";
export const REVIEW_LATENCY_P50 = "review_latency_p50_hours";
export const REVIEW_LATENCY_P90 = "review_latency_p90_hours";
export const PRS_MERGED = "prs_merged";
export const REVERT_RATE = "revert_rate";
export const REWORK_RATE = "rework_rate";
export const AI_COST_USD = "ai_cost_usd";
export const AI_TOKENS_TOTAL = "ai_tokens_total";
export const COST_PER_MERGED_PR = "cost_per_merged_pr_usd";

// Lead-time histogram, stored as per-week bucket-count metrics. Buckets in
// hours: [lo, hi); the last is open-ended (hi === null).
export interface HistBucket {
  readonly key: string;
  readonly lo: number;
  readonly hi: number | null;
  readonly label: string; // display label (frontend-only)
}

export const HIST_BUCKETS: readonly HistBucket[] = [
  { key: "pr_lead_time_hist_0_4h", lo: 0, hi: 4, label: "0–4h" },
  { key: "pr_lead_time_hist_4_24h", lo: 4, hi: 24, label: "4–24h" },
  { key: "pr_lead_time_hist_1_3d", lo: 24, hi: 72, label: "1–3d" },
  { key: "pr_lead_time_hist_3_7d", lo: 72, hi: 168, label: "3–7d" },
  { key: "pr_lead_time_hist_7_21d", lo: 168, hi: 504, label: "7–21d" },
  { key: "pr_lead_time_hist_21d_plus", lo: 504, hi: null, label: "21d+" },
] as const;

export const ALL_METRICS: readonly string[] = [
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
] as const;

// --- limitation footnotes (rendered on-chart) ---
// These strings are FROZEN: copied character-for-character from keys.py.
export const LIMITATIONS: Readonly<Record<string, string>> = {
  lead_time:
    "Start = earliest commit authored-date; rebases rewrite those dates and " +
    "long-lived branches inflate the tail. PRs without a datable first commit are excluded from the distribution. p50/p90 shown, never means.",
  review_latency:
    "First non-author review only; re-review rounds are ignored, and a " +
    "4-minute rubber stamp counts the same as a real review.",
  revert_rate:
    "Detects explicit reverts only. A fix-forward culture hides failures " +
    "from this metric entirely — it is a floor, not a truth.",
  rework_rate:
    "% of added lines modified or deleted again within 21 days. Refactors " +
    "and formatting sweeps count as rework; 21 days is a chosen window, " +
    "not a law.",
  ai_spend:
    "Spend attribution is repo-week level from opt-in resource tags; " +
    "unattributed spend makes every per-repo figure a lower bound.",
  cost_per_pr:
    "An average over a coarse join (repo-week spend / merged PRs); " +
    "unattributed spend makes it a lower bound.",
} as const;
