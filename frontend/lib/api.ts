const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  const r = await fetch(`${BASE}${path}${qs}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}
async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  registry: (v = "v1") => get<Feature[]>("/registry", { feature_version: v }),
  lineage: (v = "v1") => get<LineageGraph>("/lineage", { feature_version: v }),
  skew: (v = "v1") => get<{ report: SkewRow[] }>("/skew-report", { feature_version: v }),
  materializationLog: () => get<MatRun[]>("/materialization-log", { limit: "100" }),
  metrics: () => get<Metrics>("/metrics"),
  batch: (entity_ids: number[], v = "v1") =>
    post<BatchResult>("/features/batch", { entity_ids, feature_version: v }),
};

export interface Feature { feature_name: string; dtype: string; description: string;
  source_table: string; owner: string; tags: string[]; }
export interface LineageNode { id: string; type: "raw_table" | "feature" | "model"; }
export interface LineageGraph { nodes: LineageNode[]; edges: { source: string; target: string; type: string }[]; }
export interface SkewRow { feature_name: string; training_mean: number; serving_mean: number;
  mean_shift: number; ks_statistic: number; ks_pvalue: number; flagged: boolean; }
export interface MatRun { run_id: string; status: string; entities_processed: number;
  entities_failed: number; duration_ms: number; started_at: string; completed_at: string; }
export interface Metrics { online_store: Percentiles; on_demand: Percentiles; batch: Percentiles;
  cache_hit_rate: number; online_store_entities: number; }
export interface Percentiles { p50: number; p95: number; p99: number; count: number; }
export interface BatchResult { results: Record<string, Record<string, number> | null>;
  hits: number; misses: number; on_demand_computed: number; latency_ms: number; }
