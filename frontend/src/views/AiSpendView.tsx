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
import { AI_COST_USD, LIMITATIONS, PR_LEAD_TIME_P50 } from "../constants";
import { ChartCard } from "../components/ChartCard";
import { ChartTooltip } from "../components/ChartTooltip";
import { EmptyPlot } from "../components/EmptyPlot";
import { COLORS } from "../lib/chartTheme";
import { fmtHours, fmtUsd0 } from "../lib/format";
import { buildRows, hasAnyValue, maskedWeeks, type SeriesIndex } from "../lib/series";

const KEYS = [AI_COST_USD, PR_LEAD_TIME_P50];
const AXIS_TICK = { fill: COLORS.axisLabel, fontSize: 11 } as const;

// View 4 — the centerpiece.
// "Does spend correlate with speed — anywhere, at all?"
// AI $ spend (left axis, bars) against lead-time p50 (right axis, line).
export function AiSpendView({ idx }: { idx: SeriesIndex }) {
  const rows = buildRows(idx, KEYS);
  const masked = maskedWeeks(idx, KEYS);
  const nByLabel = new Map(rows.map((r) => [r.label, r.n]));
  const has = hasAnyValue(idx, KEYS);

  return (
    <ChartCard
      eyebrow="The centerpiece"
      featured
      question="Does AI spend correlate with speed — anywhere, at all?"
      footnote={LIMITATIONS.ai_spend}
      caption="Weekly AI spend (bars) against lead-time p50 (line). Attribution is repo-week and opt-in, so spend shown is a lower bound."
      maskedWeeks={masked}
    >
      {has ? (
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -4 }}>
            <CartesianGrid stroke={COLORS.grid} vertical={false} />
            <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: COLORS.grid }} minTickGap={16} />
            <YAxis
              yAxisId="usd"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(v: number) => fmtUsd0(v)}
            />
            <YAxis
              yAxisId="hours"
              orientation="right"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={44}
              unit="h"
            />
            <Tooltip
              cursor={{ fill: "rgba(255,255,255,0.04)" }}
              content={
                <ChartTooltip
                  format={(k, v) => (k === AI_COST_USD ? fmtUsd0(v) : fmtHours(v))}
                  meta={(l) => `n = ${nByLabel.get(l) ?? "?"}`}
                />
              }
            />
            <Bar
              yAxisId="usd"
              name="AI spend"
              dataKey={AI_COST_USD}
              fill={COLORS.spend}
              radius={[2, 2, 0, 0]}
              maxBarSize={24}
            />
            <Line
              yAxisId="hours"
              name="lead time p50"
              type="monotone"
              dataKey={PR_LEAD_TIME_P50}
              stroke={COLORS.leadLine}
              strokeWidth={2.5}
              dot={false}
              connectNulls={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      ) : (
        <EmptyPlot />
      )}
    </ChartCard>
  );
}
