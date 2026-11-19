import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { HIST_BUCKETS, LIMITATIONS, PR_LEAD_TIME_P50, PR_LEAD_TIME_P90 } from "../constants";
import { ChartCard } from "../components/ChartCard";
import { ChartTooltip } from "../components/ChartTooltip";
import { EmptyPlot } from "../components/EmptyPlot";
import { COLORS, HIST_RAMP } from "../lib/chartTheme";
import { fmtHours, fmtInt } from "../lib/format";
import {
  buildRows,
  hasAnyValue,
  maskedWeeks,
  sumHistogram,
  type SeriesIndex,
} from "../lib/series";

const KEYS = [PR_LEAD_TIME_P50, PR_LEAD_TIME_P90];
const AXIS_TICK = { fill: COLORS.axisLabel, fontSize: 11 } as const;

// View 1 — "Are we actually shipping faster, or does it just feel that way?"
// p50/p90 trend (the headline) + the summed lead-time distribution beneath it.
export function LeadTimeView({ idx }: { idx: SeriesIndex }) {
  const rows = buildRows(idx, KEYS);
  const masked = maskedWeeks(idx, KEYS);
  const hist = sumHistogram(idx, HIST_BUCKETS);
  const nByLabel = new Map(rows.map((r) => [r.label, r.n]));

  const has = hasAnyValue(idx, KEYS);

  return (
    <ChartCard
      question="Are we actually shipping faster, or does it just feel that way?"
      footnote={LIMITATIONS.lead_time}
      maskedWeeks={masked}
    >
      {has ? (
        <>
          <div className="mb-1 text-xs font-medium text-muted">
            PR lead time by week — p50 vs p90 (hours)
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: COLORS.grid }} minTickGap={16} />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} unit="h" />
              <Tooltip
                cursor={{ stroke: COLORS.axis, strokeDasharray: "3 3" }}
                content={
                  <ChartTooltip
                    format={(_k, v) => fmtHours(v)}
                    meta={(l) => `n = ${nByLabel.get(l) ?? "?"}`}
                  />
                }
              />
              <Line
                name="p50 (median)"
                type="monotone"
                dataKey={PR_LEAD_TIME_P50}
                stroke={COLORS.p50}
                strokeWidth={2}
                dot={false}
                connectNulls={false}
              />
              <Line
                name="p90 (tail)"
                type="monotone"
                dataKey={PR_LEAD_TIME_P90}
                stroke={COLORS.p90}
                strokeWidth={2}
                strokeDasharray="5 3"
                dot={false}
                connectNulls={false}
              />
            </LineChart>
          </ResponsiveContainer>

          <div className="mb-1 mt-4 text-xs font-medium text-muted">
            Distribution across the window — merged PRs by lead-time bucket
          </div>
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={hist} margin={{ top: 4, right: 12, bottom: 0, left: -8 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: COLORS.grid }} />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} allowDecimals={false} />
              <Tooltip
                cursor={{ fill: "rgba(255,255,255,0.04)" }}
                content={<ChartTooltip format={(_k, v) => `${fmtInt(v)} PRs`} />}
              />
              <Bar name="merged PRs" dataKey="count" radius={[3, 3, 0, 0]}>
                {hist.map((b) => (
                  <Cell key={b.label} fill={HIST_RAMP[b.bucketIndex] ?? COLORS.p50} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      ) : (
        <EmptyPlot />
      )}
    </ChartCard>
  );
}
