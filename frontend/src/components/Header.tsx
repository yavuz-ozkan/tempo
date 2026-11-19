import { RepoPicker } from "./RepoPicker";

interface HeaderProps {
  repos: string[];
  repo: string;
  onRepoChange: (repo: string) => void;
  rangeLabel: string;
  isMock: boolean;
  loading: boolean;
}

export function Header(props: HeaderProps) {
  const { repos, repo, onRepoChange, rangeLabel, isMock, loading } = props;
  return (
    <header className="border-b border-edge bg-panel/60 backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-4">
        <div className="mr-auto">
          <div className="flex items-center gap-2.5">
            <h1 className="text-lg font-semibold tracking-tight text-fg">Tempo</h1>
            {isMock ? (
              <span className="rounded border border-edge bg-panel-2 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted">
                mock data
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-[13px] text-muted">
            Did AI actually make us faster? — team-week grain, distributions not means.
          </p>
        </div>
        <div className="flex items-center gap-4">
          <span className="hidden text-xs text-faint sm:inline">{rangeLabel}</span>
          <RepoPicker repos={repos} value={repo} onChange={onRepoChange} disabled={loading} />
        </div>
      </div>
    </header>
  );
}
