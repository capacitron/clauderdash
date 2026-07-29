export type Range = "today" | "7d" | "30d";
export type Provider = "all" | "claude-code" | "codex" | "gemini";

export type GaugeData = {
  burn:       { value: number; today_usd: number; avg_usd: number };
  discipline: {
    value: number;
    premium_cost_pct: number;
    premium_token_pct: number;
    premium_cost_usd: number;
    target_max: number;
    families: string[];
    by_family_cost: Record<string, number>;
  };
  efficiency: { value: number; hit_rate: number };
  hygiene:    { value: number; grade: string; score: number };
  live:       { count: number };
};

export type PlanLimit = {
  kind: string;
  group: string;
  percent: number;
  severity: string;
  resets_at: string | null;
  scope_label: string | null;
};

export type PlanUsageData = {
  available: boolean;
  reason?: string;
  limits: PlanLimit[];
  extra_usage?: {
    enabled: boolean;
    utilization: number | null;
    used_credits: number | null;
    monthly_limit: number | null;
  };
  fetched_at?: string;
};

let _activeProvider: Provider = "all";

export function setActiveProvider(p: Provider) {
  _activeProvider = p;
}

export function getActiveProvider(): Provider {
  return _activeProvider;
}

export async function apiGet<T>(path: string): Promise<T> {
  let url = path;
  if (_activeProvider !== "all") {
    const sep = path.includes("?") ? "&" : "?";
    url = `${path}${sep}provider=${_activeProvider}`;
  }
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}
