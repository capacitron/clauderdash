export function shortNum(n: number): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (Math.abs(n) >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1) + "B";
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return Math.round(n).toString();
}

export function pct(n: number, decimals = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return (n * 100).toFixed(decimals) + "%";
}

export function ms(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 10_000) return (n / 1000).toFixed(1) + "s";
  if (n >= 1000) return (n / 1000).toFixed(2) + "s";
  return Math.round(n) + "ms";
}

const rel = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const units: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

export function relativeTime(iso: string | number | null | undefined): string {
  if (!iso) return "—";
  const t = typeof iso === "number" ? iso * 1000 : new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = (Date.now() - t) / 1000;
  const abs = Math.abs(diff);
  for (const [unit, sec] of units) {
    if (abs >= sec || unit === "second") {
      return rel.format(-Math.round(diff / sec), unit);
    }
  }
  return "—";
}

export function fmtCost(usd: number | null | undefined): string {
  if (usd == null || Number.isNaN(usd)) return "—";
  if (usd < 0.01) return "<$0.01";
  if (usd >= 1000) return "$" + (usd / 1000).toFixed(1) + "K";
  if (usd >= 10) return "$" + usd.toFixed(2);
  return "$" + usd.toFixed(3);
}

export function projectLabel(cwd: string | null | undefined): string {
  if (!cwd) return "(unknown)";
  const parts = cwd.replace(/^\/+|\/+$/g, "").split("/");
  return parts[parts.length - 1] || cwd;
}
