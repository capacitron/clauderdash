export type Range = "today" | "7d" | "30d";
export type Provider = "all" | "claude-code" | "codex" | "gemini";

export type GaugeData = {
  burn:       { value: number; today_usd: number; avg_usd: number };
  discipline: { value: number; opus_pct: number };
  efficiency: { value: number; hit_rate: number };
  hygiene:    { value: number; grade: string; score: number };
  live:       { count: number };
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
