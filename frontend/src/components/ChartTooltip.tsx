import { COLORS } from "../lib/chartTheme";

// A restrained tooltip shared by every chart. Recharts clones this element and
// injects `active`, `payload`, `label`, so those props are optional here.

interface TooltipEntry {
  name?: string;
  value?: number | string | null;
  color?: string;
  dataKey?: string | number;
}

export interface ChartTooltipProps {
  active?: boolean;
  payload?: readonly TooltipEntry[];
  label?: string;
  /** Per-series value formatter keyed by dataKey; falls back to raw. */
  format?: (dataKey: string, value: number) => string;
  /** Extra line under the label (e.g. "n = 5"). */
  meta?: (label: string) => string | null;
}

export function ChartTooltip(props: ChartTooltipProps) {
  const { active, payload, label, format, meta } = props;
  if (!active || !payload || payload.length === 0) return null;

  const metaLine = meta && label ? meta(label) : null;

  return (
    <div
      style={{
        background: COLORS.tooltipBg,
        border: `1px solid ${COLORS.tooltipEdge}`,
        borderRadius: 8,
        padding: "8px 10px",
        boxShadow: "0 6px 24px rgba(0,0,0,0.45)",
      }}
    >
      <div className="mb-1 text-xs font-medium text-fg">{label}</div>
      {metaLine ? <div className="mb-1 text-[11px] text-faint">{metaLine}</div> : null}
      <div className="flex flex-col gap-0.5">
        {payload.map((entry, i) => {
          const key = String(entry.dataKey ?? entry.name ?? i);
          const val = entry.value;
          const shown =
            typeof val === "number" && format ? format(key, val) : String(val ?? "—");
          return (
            <div key={key + i} className="flex items-center gap-2 text-xs">
              <span
                aria-hidden
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: 2,
                  background: entry.color ?? COLORS.axis,
                  display: "inline-block",
                }}
              />
              <span className="text-muted">{entry.name}</span>
              <span className="ml-auto font-medium text-fg">{shown}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
