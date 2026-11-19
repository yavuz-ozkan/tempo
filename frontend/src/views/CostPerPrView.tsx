import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { COST_PER_MERGED_PR, LIMITATIONS } from "../constants";
import { ChartCard } from "../components/ChartCard";
import { ChartTooltip } from "../components/ChartTooltip";
import { EmptyPlot } from "../components/EmptyPlot";
import { COLORS } from "../lib/chartTheme";
import { fmtUsd2 } from "../lib/format";
import { buildRows, hasAnyValue, maskedWeeks, type SeriesIndex } from "../lib/series";

const KEYS = [COST_PER_MERGED_PR];
const AXIS_TICK = { fill: COLORS.axisLabel, fontSize: 11 } as const;

// View 5 — "What does a merged PR cost now, tokens included?"
// Cost per merged PR trend, with the unattributed-spend caveat on-chart.
export function CostPerPrView({ idx }: { idx: SeriesIndex }) {
  const rows = buildRows(idx, KEYS);
  const masked = maskedWeeks(idx, KEYS);
  const nByLabel = new Map(rows.map((r) => [r.label, r.n]));
  const has = hasAnyValue(idx, KEYS);

  return (
    <ChartCard
      question="What does a merged PR cost now, tokens included?"
      footnote={LIMITATIONS.cost_per_pr}
      caption="Lower bound: AI spend that is not tagged to a repo is excluded, so the true cost per PR is higher than shown."
      maskedWeeks={masked}
    >
      {has ? (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={COLORS.grid} vertical={false} />
            <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: COLORS.grid }} minTickGap={16} />
            <YAxis
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={56}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`}
            />
            <ReferenceLine y={0} stroke={COLORS.grid} />
            <Tooltip
              cursor={{ stroke: COLORS.axis, strokeDasharray: "3 3" }}
              content={
                <ChartTooltip
                  format={(_k, v) => `${fmtUsd2(v)} / PR`}
                  meta={(l) => `n = ${nByLabel.get(l) ?? "?"} · lower bound`}
                />
              }
            />
            <Line
              name="cost per merged PR"
              type="monotone"
              dataKey={COST_PER_MERGED_PR}
              stroke={COLORS.cost}
              strokeWidth={2}
              dot={false}
              connectNulls={false}
            />
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <EmptyPlot />
      )}
    </ChartCard>
  );
}
