import type { ReactNode } from "react";
import { InsufficientNBadge } from "./InsufficientNBadge";

// Every view is a ChartCard. Invariants:
//  - title IS the question the chart answers
//  - the frozen LIMITATIONS footnote is always rendered
//  - masked (n < N_FLOOR) weeks surface as a subtle badge
//  - an optional on-chart caption carries per-chart caveats (e.g. lower-bound)

interface ChartCardProps {
  question: string;
  footnote: string;
  maskedWeeks: string[];
  /** Small label above the question (e.g. "The centerpiece"). */
  eyebrow?: string;
  /** On-chart caveat rendered above the plot (e.g. unattributed-spend note). */
  caption?: string;
  /** Emphasise the card (used for the centerpiece chart). */
  featured?: boolean;
  children: ReactNode;
}

export function ChartCard(props: ChartCardProps) {
  const { question, footnote, maskedWeeks, eyebrow, caption, featured, children } =
    props;
  return (
    <section
      className={[
        "flex flex-col rounded-xl border bg-panel p-5 shadow-sm",
        featured ? "border-p50/30 ring-1 ring-p50/10" : "border-edge",
      ].join(" ")}
    >
      <header className="mb-3 flex items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow ? (
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-p50">
              {eyebrow}
            </div>
          ) : null}
          <h2 className="text-[15px] font-semibold leading-snug text-fg">{question}</h2>
        </div>
        <InsufficientNBadge weeks={maskedWeeks} />
      </header>

      {caption ? (
        <p className="mb-2 text-xs text-muted">{caption}</p>
      ) : null}

      <div className="min-w-0 flex-1">{children}</div>

      <footer className="mt-3 border-t border-edge-soft pt-3">
        <p className="text-[11px] leading-relaxed text-faint">
          <span className="font-medium text-muted">Limitation. </span>
          {footnote}
        </p>
      </footer>
    </section>
  );
}
