import { useEffect, useState } from "react";
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
import { shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Resp = {
  range: string;
  days: number;
  series: {
    day: string;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_create_tokens: number;
  }[];
  totals: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_create_tokens: number;
    grand_total: number;
  };
};

export function TokenUsage() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/tokens?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const total = data?.totals.grand_total ?? 0;
  return (
    <Panel
      kicker="02 · TOKENS"
      title="Token usage"
      subtitle="Input + output + cache, by day"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={4} />
      ) : data.series.length === 0 ? (
        <EmptyState label="No token data yet." />
      ) : (
        <>
          <div className="mb-4 flex items-baseline gap-4">
            <div>
              <div className="stat-num">{shortNum(total)}</div>
              <div className="kicker">Total tokens</div>
            </div>
            <TokenLegend totals={data.totals} />
          </div>
          <div className="flex-1">
            <ResponsiveContainer width="99%" height={160}>
              <BarChart data={data.series} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: "#5a5a70", fontSize: 10, fontFamily: "JetBrains Mono" }}
                  axisLine={{ stroke: "#2a2a3d" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "#5a5a70", fontSize: 10, fontFamily: "JetBrains Mono" }}
                  tickFormatter={shortNum}
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
                  formatter={(v: number) => shortNum(v)}
                />
                <Bar isAnimationActive={false} dataKey="cache_read_tokens" stackId="a" fill="#10b981" />
                <Bar isAnimationActive={false} dataKey="cache_create_tokens" stackId="a" fill="#06b6d4" />
                <Bar isAnimationActive={false} dataKey="input_tokens" stackId="a" fill="#4d7cff" />
                <Bar isAnimationActive={false} dataKey="output_tokens" stackId="a" fill="#8b5cf6" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Panel>
  );
}

function TokenLegend({ totals }: { totals: Resp["totals"] }) {
  const items = [
    { label: "Cache read", color: "#10b981", v: totals.cache_read_tokens },
    { label: "Cache create", color: "#06b6d4", v: totals.cache_create_tokens },
    { label: "Input", color: "#4d7cff", v: totals.input_tokens },
    { label: "Output", color: "#8b5cf6", v: totals.output_tokens },
  ];
  return (
    <div className="flex flex-wrap gap-3">
      {items.map((i) => (
        <div key={i.label} className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm" style={{ background: i.color }} />
          <span className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
            {i.label}
          </span>
          <span className="font-mono text-[11px] text-text-dim">{shortNum(i.v)}</span>
        </div>
      ))}
    </div>
  );
}
