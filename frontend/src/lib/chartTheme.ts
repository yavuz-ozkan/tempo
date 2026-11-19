// Chart series palette lives here (single source for Recharts stroke/fill
// colours). UI chrome colours — panels, edges, text — live in index.css as
// Tailwind theme tokens. Series colours are chosen semantically, not
// decoratively: cool = fast/cheap/good, warm = slow/expensive/reworked.

export const COLORS = {
  p50: "#38bdf8", // sky   — the median, the headline line
  p90: "#fbbf24", // amber — the tail; warmer because the tail is the worry
  volume: "#3f4a5f", // muted slate — secondary-axis volume bars, low emphasis
  spend: "#a78bfa", // violet — AI $ spend
  leadLine: "#38bdf8", // sky — lead time, kept identical to p50 for continuity
  rework: "#fb7185", // rose — a defect-ish signal
  cost: "#f0abfc", // orchid — cost per PR
  mask: "#f59e0b", // amber-600 — the "insufficient n" accent
  grid: "#1e2634",
  axis: "#626c80",
  axisLabel: "#8a93a6",
  tooltipBg: "#0d121c",
  tooltipEdge: "#2a3444",
} as const;

// Sequential ramp for the six lead-time histogram buckets: fast (emerald)
// through slow (deep red). Order matches keys.HIST_BUCKETS.
export const HIST_RAMP: readonly string[] = [
  "#34d399", // 0–4h
  "#a3e635", // 4–24h
  "#fbbf24", // 1–3d
  "#fb923c", // 3–7d
  "#f87171", // 7–21d
  "#dc2626", // 21d+
] as const;
