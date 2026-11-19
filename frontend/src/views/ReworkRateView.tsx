import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LIMITATIONS, REWORK_RATE } from "../constants";
import { ChartCard } from "../components/ChartCard";
import { ChartTooltip } from "../components/ChartTooltip";
import { EmptyPlot } from "../components/EmptyPlot";
import { COLORS } from "../lib/chartTheme";
import { fmtPct } from "../lib/format";
import { buildRows, hasAnyValue, maskedWeeks, type SeriesIndex } from "../lib/series";

const KEYS = [REWORK_RATE];
const AXIS_TICK = { fill: COLORS.axisLabel, fontSize: 11 } as const;

// View 3 — "Is the code we merge now actually surviving?"
// % of added lines reworked within 21 days, by week.
export function ReworkRateView({ idx }: { idx: SeriesIndex }) {
  const rows = buildRows(idx, KEYS);
  const masked = maskedWeeks(idx, KEYS);
  const nByLabel = new Map(rows.map((r) => [r.label, r.n]));
  const has = hasAnyValue(idx, KEYS);

  return (
    <ChartCard
      question="Is the code we merge now actually surviving?"
      footnote={LIMITATIONS.rework_rate}
      maskedWeeks={masked}
    >
      {has ? (
        <>
          <div className="mb-1 text-xs font-medium text-muted">
            Rework rate by week — % of added lines modified or deleted within 21 days
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -4 }}>
              <defs>
                <linearGradient id="reworkFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={COLORS.rework} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={COLORS.rework} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: COLORS.grid }} minTickGap={16} />
              <YAxis
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={48}
                tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
              />
              <Tooltip
                cursor={{ stroke: COLORS.axis, strokeDasharray: "3 3" }}
                content={
                  <ChartTooltip
                    format={(_k, v) => fmtPct(v)}
                    meta={(l) => `n = ${nByLabel.get(l) ?? "?"}`}
                  />
                }
              />
              <Area
                type="monotone"
                dataKey={REWORK_RATE}
                stroke="none"
                fill="url(#reworkFill)"
                connectNulls={false}
                activeDot={false}
                legendType="none"
                isAnimationActive={false}
              />
              <Line
                name="rework rate"
                type="monotone"
                dataKey={REWORK_RATE}
                stroke={COLORS.rework}
                strokeWidth={2}
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
