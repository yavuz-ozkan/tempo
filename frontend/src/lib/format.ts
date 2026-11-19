// Value + label formatting. Percentile values are hours; rates are fractions
// in [0,1]; spend is USD. No mean is ever computed here — the frontend only
// ever formats p50/p90 the rollup already produced.

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** "2026-06-29" -> "Jun 29" (no date library — the string is always ISO). */
export function formatWeek(iso: string): string {
  const [y, m, d] = iso.split("-").map((s) => Number.parseInt(s, 10));
  if (!y || !m || !d) return iso;
  return `${MONTHS[m - 1]} ${d}`;
}

export function fmtHours(v: number): string {
  if (v >= 48) return `${(v / 24).toFixed(1)}d`;
  return `${v.toFixed(1)}h`;
}

/** rate stored as a fraction in [0,1] -> percent string. */
export function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function fmtUsd0(v: number): string {
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

export function fmtUsd2(v: number): string {
  return `$${v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function fmtInt(v: number): string {
  return Math.round(v).toLocaleString("en-US");
}
