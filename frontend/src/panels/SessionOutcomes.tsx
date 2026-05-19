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
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Row = {
  day: string;
  errored: number;
  rate_limited: number;
  truncated: number;
  unfinished: number;
  ok: number;
};

export function SessionOutcomes() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<{ series: Row[] } | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<{ series: Row[] }>(`/api/outcomes?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  return (
    <Panel
      kicker="05 · OUTCOMES"
      title="Session outcomes"
      subtitle="Errored · rate_limited · truncated · unfinished · ok"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={4} />
      ) : data.series.length === 0 ? (
        <EmptyState label="No sessions in this range." />
      ) : (
        <div className="flex-1">
          <ResponsiveContainer width="99%" height={180}>
            <BarChart data={data.series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fill: "#5a5a70", fontSize: 10, fontFamily: "JetBrains Mono" }}
                axisLine={{ stroke: "#2a2a3d" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: "#5a5a70", fontSize: 10, fontFamily: "JetBrains Mono" }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: "rgba(77,124,255,0.06)" }}
                contentStyle={{
                  background: "#12121a",
                  border: "1px solid #2a2a3d",
                  borderRadius: 8,
                  fontSize: 12,
                }}
              />
              <Bar isAnimationActive={false} dataKey="errored" stackId="o" fill="#ef4444" />
              <Bar isAnimationActive={false} dataKey="rate_limited" stackId="o" fill="#f59e0b" />
              <Bar isAnimationActive={false} dataKey="truncated" stackId="o" fill="#ec4899" />
              <Bar isAnimationActive={false} dataKey="unfinished" stackId="o" fill="#8b5cf6" />
              <Bar isAnimationActive={false} dataKey="ok" stackId="o" fill="#10b981" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
