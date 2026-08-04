export type Range = "today" | "7d" | "30d";
export type Provider = "all" | "claude-code" | "codex" | "gemini";

export type GaugeData = {
  burn: { value: number; today_usd: number; avg_usd: number };
  discipline: {
    value: number;
    worst_family: string;
    worst_ratio: number;
    premium_cost_pct: number;
    premium_token_pct: number;
    premium_cost_usd: number;
    families: string[];
    family_compliance: Record<
      string,
      { cost_pct: number; cost_usd: number; target_max: number; ratio: number }
    >;
    by_family_cost: Record<string, number>;
  };
  efficiency: { value: number; hit_rate: number };
  hygiene: {
    value: number;
    grade: string;
    score: number;
    checks_failing: number;
    checks_total: number;
  };
  live: { count: number };
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

// Write calls carry the fix token. Any custom request header forces a CORS
// preflight, and the backend mounts no CORS middleware, so a page on another
// origin cannot send this even though it can reach localhost — see
// backend/app/api/fixauth.py. Fetched once and cached; the token only rotates
// if data/.fix-token is deleted, which requires a restart anyway.
let _fixToken: Promise<string> | null = null;

function fixToken(): Promise<string> {
  if (!_fixToken) {
    _fixToken = apiGet<{ token: string }>("/api/fix-token")
      .then((r) => r.token)
      .catch((e) => {
        _fixToken = null; // don't cache a failure; let the next click retry
        throw e;
      });
  }
  return _fixToken;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const token = await fixToken();
  const res = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Dashboard-Token": token,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    // FastAPI puts the useful part in `detail`; surface it rather than a bare
    // status, since these are operator-facing errors ("not on the allowlist").
    const detail = await res
      .json()
      .then((d: { detail?: string }) => d.detail)
      .catch(() => null);
    throw new Error(detail || `${path} → ${res.status}`);
  }
  return res.json();
}
