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

type Resp = {
  series: { day: string; opus: number; sonnet: number; haiku: number; unknown: number }[];
  totals: { opus: number; sonnet: number; haiku: number; unknown: number };
  total_cost_usd: number;
  opus_pct_overall: number;
  opus_target_max: number;
};

export function ModelMix() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/models?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const overOpus = data && data.opus_pct_overall > data.opus_target_max;
  const tone = !data
    ? "text-text"
    : overOpus
    ? "text-accent-red"
    : data.opus_pct_overall > 0.15
    ? "text-accent-orange"
    : "text-accent-green";

  function opusNarrative(opusPct: number, target: number): string {
    const mult = (opusPct / target).toFixed(1);
    if (opusPct > target) return `${mult}× over the ${target}% target — Sonnet handles most tasks at 1/5 the cost.`;
    if (opusPct > target * 0.75) return `Close to the ${target}% target. Watch for upward drift.`;
    return `Well within the ${target}% target. Model mix is healthy.`;
  }

  return (
    <Panel
      kicker="08 · MODELS"
      title="Model mix"
      subtitle="Opus target ≤ 20% of tokens"
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
                <div className={clsx("stat-num", tone)}>{pct(data.opus_pct_overall, 1)}</div>
                <div className="kicker">Opus share</div>
              </div>
              {overOpus && (
                <span className="chip bg-accent-red/10 text-accent-red">Over target</span>
              )}
            </div>
            <p className="stat-narrative">
              {opusNarrative(data.opus_pct_overall, data.opus_target_max)}
            </p>
            {data.total_cost_usd > 0 && (
              <div className="mt-1 font-mono text-xs text-accent-cyan">
                {fmtCost(data.total_cost_usd)} this period
              </div>
            )}
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
                <Bar isAnimationActive={false} dataKey="haiku" stackId="m" fill="#5b9bd5" />
                <Bar isAnimationActive={false} dataKey="sonnet" stackId="m" fill="#9b8aaa" />
                <Bar isAnimationActive={false} dataKey="opus" stackId="m" fill="#e05252" />
                <Bar isAnimationActive={false} dataKey="unknown" stackId="m" fill="#405060" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Panel>
  );
}
