import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import clsx from "clsx";
import { apiGet, Range } from "../lib/api";
import { pct } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Resp = {
  overall_hit_rate: number;
  target: number;
  total_billable_tokens: number;
  low_sample: boolean;
  series: { day: string; hit_rate: number; billable_tokens: number; low_sample: boolean }[];
};

export function CacheEfficiency() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/cache?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const tone = !data
    ? "text-text"
    : data.overall_hit_rate >= data.target
    ? "text-accent-green"
    : data.overall_hit_rate >= 0.5
    ? "text-accent-orange"
    : "text-accent-red";

  return (
    <Panel
      kicker="06 · CACHE"
      title="Cache efficiency"
      subtitle="Target ≥ 70% hit rate"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={4} />
      ) : data.series.length === 0 ? (
        <EmptyState label="No cache data in this range." />
      ) : (
        <>
          <div className="mb-4 flex items-baseline gap-4">
            <div>
              <div className={clsx("stat-num", tone)}>{pct(data.overall_hit_rate, 1)}</div>
              <div className="kicker">Hit rate</div>
            </div>
            {data.low_sample && (
              <span className="chip bg-accent-orange/10 text-accent-orange">Low sample</span>
            )}
          </div>
          <div className="flex-1">
            <ResponsiveContainer width="99%" height={140}>
              <AreaChart data={data.series}>
                <defs>
                  <linearGradient id="cacheArea" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: "#5a5a70", fontSize: 10, fontFamily: "JetBrains Mono" }}
                  axisLine={{ stroke: "#2a2a3d" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "#5a5a70", fontSize: 10, fontFamily: "JetBrains Mono" }}
                  tickFormatter={(v) => (v * 100).toFixed(0) + "%"}
                  domain={[0, 1]}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "rgba(77,124,255,0.06)" }}
                  contentStyle={{
                    background: "#12121a",
                    border: "1px solid #2a2a3d",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(v: number) => pct(v, 1)}
                />
                <Area
                  isAnimationActive={false}
                  type="monotone"
                  dataKey="hit_rate"
                  stroke="#10b981"
                  fill="url(#cacheArea)"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Panel>
  );
}
