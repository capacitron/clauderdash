import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet, Range } from "../lib/api";
import { pct, fmtCost } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type FamilyTotals = Record<string, number>;

type PremiumBreakdown = {
  cost_usd: number;
  cost_share: number;
  target_max: number;
};

type Resp = {
  families: string[];
  premium_families: string[];
  premium_breakdown: Record<string, PremiumBreakdown>;
  series: (FamilyTotals & { day: string })[];
  totals: FamilyTotals;
  total_cost_usd: number;
  premium_cost_usd: number;
  premium_cost_share: number;
};

const FAMILY_COLORS: Record<string, string> = {
  haiku: "#5b9bd5",
  sonnet: "#9b8aaa",
  opus: "#e05252",
  fable: "#c4788a",
  unknown: "#405060",
};
const FALLBACK_COLOR = "#6e8aa0";

// The backend sends `families` cheapest-last; reverse it so the premium tiers
// stack on top of the bar. Derived from the payload rather than hardcoded — a
// family added server-side must not silently vanish from the chart, which is
// exactly how Fable went missing before.
const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function stackOrder(families: string[]): string[] {
  const rest = families.filter((f) => f !== "unknown");
  return [...rest].reverse().concat(families.includes("unknown") ? ["unknown"] : []);
}

export function ModelMix() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/models?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  function toneFor(share: number, target: number): string {
    if (share > target) return "text-accent-red";
    if (share > target * 0.75) return "text-accent-orange";
    return "text-accent-green";
  }

  function narrative(share: number, target: number): string {
    const targetPct = (target * 100).toFixed(0);
    const mult = (share / target).toFixed(1);
    if (share > target)
      return `${mult}× over its ${targetPct}% target — Sonnet handles most tasks at a fraction of the cost.`;
    if (share > target * 0.75) return `Close to its ${targetPct}% target.`;
    return `Well within its ${targetPct}% target.`;
  }

  // Only chart families that actually appear — an empty `unknown` bucket would
  // otherwise add a dead legend entry to every stack.
  const activeFamilies = data
    ? stackOrder(data.families ?? []).filter((f) => (data.totals[f] ?? 0) > 0)
    : [];

  return (
    <Panel
      kicker="08 · MODELS"
      title="Model mix"
      subtitle={
        data
          ? data.premium_families
              .map((f) => `${titleCase(f)} ≤ ${(data.premium_breakdown[f].target_max * 100).toFixed(0)}%`)
              .join(" · ") + " of spend"
          : "Premium model spend share"
      }
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={4} />
      ) : data.series.length === 0 ? (
        <EmptyState label="No assistant messages with model data yet." />
      ) : (
        <>
          <div className="mb-3">
            <div className="flex items-baseline gap-4">
              <div>
                <div className="stat-num text-text">{pct(data.premium_cost_share, 1)}</div>
                <div className="kicker">Combined premium spend</div>
              </div>
              {data.total_cost_usd > 0 && (
                <div className="font-mono text-xs text-accent-cyan">
                  {fmtCost(data.premium_cost_usd)} of {fmtCost(data.total_cost_usd)} this period
                </div>
              )}
            </div>

            {/* Each premium family has its own ceiling — Fable (~5x Sonnet's
                price) and Opus (~2.5x) don't share one target anymore, so
                compliance is reported per family, not as one blended number. */}
            <div className="mt-2.5 flex flex-col gap-2">
              {data.premium_families.map((fam) => {
                const b = data.premium_breakdown[fam];
                if (!b) return null;
                const over = b.cost_share > b.target_max;
                return (
                  <div key={fam} className="rounded-lg border border-border bg-surface-2/40 p-2.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="font-mono text-xs font-medium text-text">
                        {titleCase(fam)} <span className={clsx("font-semibold", toneFor(b.cost_share, b.target_max))}>{pct(b.cost_share, 1)}</span>
                      </span>
                      {over && (
                        <span className="chip bg-accent-red/10 text-accent-red">Over target</span>
                      )}
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-text-dim">
                      {narrative(b.cost_share, b.target_max)}
                    </p>
                  </div>
                );
              })}
            </div>

            <div className="mt-2.5 flex flex-wrap gap-2">
              {activeFamilies.map((f) => (
                <span key={f} className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-text-dim">
                  <span
                    className="inline-block h-2 w-2 rounded-sm"
                    style={{ background: FAMILY_COLORS[f] ?? FALLBACK_COLOR }}
                  />
                  {f}
                </span>
              ))}
            </div>
          </div>
          <div className="flex-1">
            <ResponsiveContainer width="99%" height={140}>
              <BarChart data={data.series}>
                <CartesianGrid strokeDasharray="3 3" stroke="#243040" vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: "#405060", fontSize: 10, fontFamily: "JetBrains Mono" }}
                  axisLine={{ stroke: "#243040" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "#405060", fontSize: 10, fontFamily: "JetBrains Mono" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "rgba(34,211,238,0.05)" }}
                  contentStyle={{
                    background: "#0d1117",
                    border: "1px solid #243040",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                {activeFamilies.map((f) => (
                  <Bar
                    key={f}
                    isAnimationActive={false}
                    dataKey={f}
                    stackId="m"
                    fill={FAMILY_COLORS[f] ?? FALLBACK_COLOR}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Panel>
  );
}
