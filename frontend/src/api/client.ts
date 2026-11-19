import type {
  MetricsQuery,
  MetricsResponse,
  ReposResponse,
  TempoApi,
} from "../types";

// Real fetch layer. Talks to the API serving endpoints through the Vite
// dev proxy (/api -> http://localhost:8010). Shares the TempoApi interface
// with the mock so views never know which one they got.

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const httpApi: TempoApi = {
  async getRepos(): Promise<string[]> {
    const data = await getJson<ReposResponse>("/api/repos");
    return data.repos;
  },

  async getMetrics(query: MetricsQuery): Promise<MetricsResponse> {
    const params = new URLSearchParams();
    params.set("repo", query.repo);
    if (query.from) params.set("from", query.from);
    if (query.to) params.set("to", query.to);
    if (query.metrics && query.metrics.length > 0) {
      params.set("metrics", query.metrics.join(","));
    }
    return getJson<MetricsResponse>(`/api/metrics?${params.toString()}`);
  },
};
