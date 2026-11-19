import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  LIMITATIONS,
  PRS_MERGED,
  REVIEW_LATENCY_P50,
  REVIEW_LATENCY_P90,
} from "../constants";
import { ChartCard } from "../components/ChartCard";
import { ChartTooltip } from "../components/ChartTooltip";
import { EmptyPlot } from "../components/EmptyPlot";
import { COLORS } from "../lib/chartTheme";
import { fmtHours, fmtInt } from "../lib/format";
import { buildRows, hasAnyValue, maskedWeeks, type SeriesIndex } from "../lib/series";

const KEYS = [REVIEW_LATENCY_P50, REVIEW_LATENCY_P90, PRS_MERGED];
const AXIS_TICK = { fill: COLORS.axisLabel, fontSize: 11 } as const;

// View 2 — "Is review becoming the bottleneck as AI raises PR volume?"
// p50/p90 review latency (left axis, hours) over merged-PR volume (right axis).
export function ReviewLatencyView({ idx }: { idx: SeriesIndex }) {
  const rows = buildRows(idx, KEYS);
  const masked = maskedWeeks(idx, KEYS);
  const nByLabel = new Map(rows.map((r) => [r.label, r.n]));
  const has = hasAnyValue(idx, [REVIEW_LATENCY_P50, REVIEW_LATENCY_P90]);

  return (
    <ChartCard
      question="Is review becoming the bottleneck as AI raises PR volume?"
      footnote={LIMITATIONS.review_latency}
      maskedWeeks={masked}
    >
      {has ? (
        <>
          <div className="mb-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            <span>Left axis: review latency (hours), p50 / p90.</span>
            <span>Right axis: merged-PR volume.</span>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: COLORS.grid }} minTickGap={16} />
              <YAxis
                yAxisId="hours"
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={44}
                unit="h"
              />
              <YAxis
                yAxisId="vol"
                orientation="right"
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={36}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ stroke: COLORS.axis, strokeDasharray: "3 3" }}
                content={
                  <ChartTooltip
                    format={(k, v) => (k === PRS_MERGED ? `${fmtInt(v)} PRs` : fmtHours(v))}
                    meta={(l) => `n = ${nByLabel.get(l) ?? "?"}`}
                  />
                }
              />
              <Bar
                yAxisId="vol"
                name="merged PRs"
                dataKey={PRS_MERGED}
                fill={COLORS.volume}
                radius={[2, 2, 0, 0]}
                maxBarSize={22}
              />
              <Line
                yAxisId="hours"
                name="p50 (median)"
                type="monotone"
                dataKey={REVIEW_LATENCY_P50}
                stroke={COLORS.p50}
                strokeWidth={2}
                dot={false}
                connectNulls={false}
              />
              <Line
                yAxisId="hours"
                name="p90 (tail)"
                type="monotone"
                dataKey={REVIEW_LATENCY_P90}
                stroke={COLORS.p90}
                strokeWidth={2}
                strokeDasharray="5 3"
                dot={false}
                connectNulls={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </>
      ) : (
        <EmptyPlot />
      )}
    </ChartCard>
  );
}
