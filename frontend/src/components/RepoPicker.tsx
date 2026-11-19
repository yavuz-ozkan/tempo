// Repo picker fed by GET /api/repos. Native select, styled to match the dark
// chrome — no component-library dependency (keep deps lean).

interface RepoPickerProps {
  repos: string[];
  value: string;
  onChange: (repo: string) => void;
  disabled?: boolean;
}

export function RepoPicker({ repos, value, onChange, disabled }: RepoPickerProps) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted">Repo</span>
      <select
        className="rounded-md border border-edge bg-panel-2 px-2.5 py-1.5 text-sm text-fg outline-none focus:border-p50 disabled:opacity-50"
        value={value}
        disabled={disabled || repos.length === 0}
        onChange={(e) => onChange(e.target.value)}
      >
        {repos.length === 0 ? <option value="">(no repos)</option> : null}
        {repos.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
    </label>
  );
}
