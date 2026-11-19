import { useEffect, useMemo, useState } from "react";
import { api, IS_MOCK } from "./api";
import { ALL_METRICS } from "./constants";
import { Header } from "./components/Header";
import { indexSeries, type SeriesIndex } from "./lib/series";
import type { MetricsResponse } from "./types";
import { AiSpendView } from "./views/AiSpendView";
import { CostPerPrView } from "./views/CostPerPrView";
import { LeadTimeView } from "./views/LeadTimeView";
import { ReviewLatencyView } from "./views/ReviewLatencyView";
import { ReworkRateView } from "./views/ReworkRateView";

const RANGE_WEEKS = 26;

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultRange(): { from: string; to: string } {
  const now = new Date();
  const from = new Date(now);
  from.setUTCDate(from.getUTCDate() - RANGE_WEEKS * 7);
  return { from: isoDate(from), to: isoDate(now) };
}

export default function App() {
  const range = useMemo(defaultRange, []);
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState<string>("");
  const [resp, setResp] = useState<MetricsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Load the repo list once. Default to the first repo.
  useEffect(() => {
    let live = true;
    api
      .getRepos()
      .then((list) => {
        if (!live) return;
        setRepos(list);
        setRepo((cur) => cur || list[0] || "");
        if (list.length === 0) setLoading(false);
      })
      .catch((e: unknown) => {
        if (!live) return;
        setError(describe(e));
        setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  // Load metrics whenever the selected repo changes.
  useEffect(() => {
    if (!repo) return;
    let live = true;
    setLoading(true);
    setError(null);
    api
      .getMetrics({ repo, from: range.from, to: range.to, metrics: ALL_METRICS })
      .then((data) => {
        if (!live) return;
        setResp(data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!live) return;
        setError(describe(e));
        setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [repo, range.from, range.to]);

  const idx: SeriesIndex | null = useMemo(
    () => (resp ? indexSeries(resp) : null),
    [resp],
  );

  const rangeLabel = `Last ${RANGE_WEEKS} weeks · ${range.from} → ${range.to}`;

  return (
    <div className="min-h-full">
      <Header
        repos={repos}
        repo={repo}
        onRepoChange={setRepo}
        rangeLabel={rangeLabel}
        isMock={IS_MOCK}
        loading={loading}
      />

      <main className="mx-auto max-w-5xl px-6 py-6">
        {error ? (
          <ErrorCard message={error} />
        ) : loading && !idx ? (
          <LoadingCard />
        ) : !idx || repos.length === 0 ? (
          <EmptyCard />
        ) : (
          <div className="grid grid-cols-1 gap-6">
            <LeadTimeView idx={idx} />
            <ReviewLatencyView idx={idx} />
            <ReworkRateView idx={idx} />
            <AiSpendView idx={idx} />
            <CostPerPrView idx={idx} />
          </div>
        )}
      </main>

      <footer className="mx-auto max-w-5xl px-6 pb-10 pt-2">
        <p className="text-[11px] leading-relaxed text-faint">
          Team-level only. No per-person view exists — the serving API cannot
          produce one. Slices under three contributors are masked as
          "insufficient n"; every chart shows distributions (p50/p90), never means.
        </p>
      </footer>
    </div>
  );
}

function describe(e: unknown): string {
  const base = e instanceof Error ? e.message : String(e);
  if (IS_MOCK) return base;
  return `${base}. The backend may be offline — run with VITE_MOCK=1 to preview against the built-in fixture.`;
}

function LoadingCard() {
  return (
    <div className="flex h-64 items-center justify-center rounded-xl border border-edge bg-panel text-sm text-muted">
      Loading metrics…
    </div>
  );
}

function EmptyCard() {
  return (
    <div className="flex h-64 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-edge bg-panel text-sm text-muted">
      <span>No repositories with metrics yet.</span>
      <span className="text-faint">Run the collectors and rollup, or preview with VITE_MOCK=1.</span>
    </div>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-rework/40 bg-rework/5 p-5">
      <div className="mb-1 text-sm font-semibold text-fg">Could not load metrics</div>
      <p className="text-[13px] leading-relaxed text-muted">{message}</p>
    </div>
  );
}
