import { N_FLOOR } from "../constants";
import { maskedWeekLabels } from "../lib/series";

// A subtle badge listing the weeks the rollup masked because fewer
// than N_FLOOR contributors backed the slice. The chart itself shows a gap in
// the line; this names the gaps without shouting.

export function InsufficientNBadge({ weeks }: { weeks: string[] }) {
  if (weeks.length === 0) return null;
  const labels = maskedWeekLabels(weeks);
  const listed = labels.join(", ");
  return (
    <span
      title={`Masked (n < ${N_FLOOR}): ${listed}`}
      className="inline-flex items-center gap-1.5 rounded-full border border-mask/40 bg-mask/10 px-2 py-0.5 text-[11px] font-medium text-mask"
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-mask" />
      insufficient n · {weeks.length} week{weeks.length === 1 ? "" : "s"} masked
    </span>
  );
}
