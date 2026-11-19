// Shown when a view's metric has no non-null points in range — distinct from
// a masked week (which shows a gap + badge). "No data" is never drawn as zero.

export function EmptyPlot({ height = 240 }: { height?: number }) {
  return (
    <div
      className="flex items-center justify-center rounded-lg border border-dashed border-edge text-sm text-faint"
      style={{ height }}
    >
      No data for this metric in the selected range.
    </div>
  );
}
