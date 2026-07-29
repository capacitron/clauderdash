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

type Resp = {
  families: string[];
  premium_families: string[];
  series: (FamilyTotals & { day: string })[];
  totals: FamilyTotals;
  total_cost_usd: number;
  premium_cost_usd: number;
  premium_cost_share: number;
  premium_target_max: number;
};

// Stack order is cheapest-first so the premium tiers sit on top of the bar.
const FAMILY_COLORS: Record<string, string> = {
  haiku: "#5b9bd5",
  sonnet: "#9b8aaa",
  opus: "#e05252",
  fable: "#c4788a",
  unknown: "#405060",
};
const STACK_ORDER = ["haiku", "sonnet", "opus", "fable", "unknown"];

export function ModelMix() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/models?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const over = data && data.premium_cost_share > data.premium_target_max;
  const tone = !data
    ? "text-text"
    : over
    ? "text-accent-red"
    : data.premium_cost_share > data.premium_target_max * 0.75
    ? "text-accent-orange"
    : "text-accent-green";

  function narrative(share: number, target: number): string {
    // target arrives as a fraction; render it as a percentage.
    const targetPct = (target * 100).toFixed(0);
    const mult = (share / target).toFixed(1);
    if (share > target)
      return `${mult}× over the ${targetPct}% target — Sonnet handles most tasks at a fraction of the cost.`;
    if (share > target * 0.75) return `Close to the ${targetPct}% target. Watch for upward drift.`;
    return `Well within the ${targetPct}% target. Model mix is healthy.`;
  }

  // Only chart families that actually appear — an empty `unknown` bucket would
  // otherwise add a dead legend entry to every stack.
  const activeFamilies = data
    ? STACK_ORDER.filter((f) => (data.totals[f] ?? 0) > 0)
    : [];

  return (
    <Panel
      kicker="08 · MODELS"
      title="Model mix"
      subtitle="Premium (Fable + Opus) target ≤ 20% of spend"
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
                <div className={clsx("stat-num", tone)}>{pct(data.premium_cost_share, 1)}</div>
                <div className="kicker">Premium spend share</div>
              </div>
              {over && (
                <span className="chip bg-accent-red/10 text-accent-red">Over target</span>
              )}
            </div>
            <p className="stat-narrative">
              {narrative(data.premium_cost_share, data.premium_target_max)}
            </p>
            {data.total_cost_usd > 0 && (
              <div className="mt-1 font-mono text-xs text-accent-cyan">
                {fmtCost(data.premium_cost_usd)} premium of {fmtCost(data.total_cost_usd)} this period
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              {activeFamilies.map((f) => (
                <span key={f} className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-text-dim">
                  <span
                    className="inline-block h-2 w-2 rounded-sm"
                    style={{ background: FAMILY_COLORS[f] }}
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
                    fill={FAMILY_COLORS[f]}
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
