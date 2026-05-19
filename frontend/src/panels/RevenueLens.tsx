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

type TagRow = { tag: string; sessions: number; tokens: number };
type Resp = {
  range: string;
  days: number;
  tags: TagRow[];
  untagged: { sessions: number; tokens: number };
};

export function RevenueLens() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/revenue-lens?range=${range}`)
      .then(setData)
      .catch(() => setData(null));
  }, [range]);

  const rows: TagRow[] =
    data && data.tags.length > 0
      ? [...data.tags]
      : [];
  if (data && data.untagged.tokens > 0) {
    rows.push({
      tag: "untagged",
      sessions: data.untagged.sessions,
      tokens: data.untagged.tokens,
    });
  }

  return (
    <Panel
      kicker="17 · LENS"
      title="Lens"
      subtitle="Tokens by cwd → tag (PROJECT_TAG_RULES)"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={4} />
      ) : rows.length === 0 ? (
        <EmptyState label="No tagged activity in this range." />
      ) : (
        <div className="flex-1">
          <ResponsiveContainer width="99%" height={200}>
            <BarChart data={rows} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3d" vertical={false} />
              <XAxis
                dataKey="tag"
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
                formatter={(v: number, name: string) =>
                  name === "tokens" ? [shortNum(v), "tokens"] : [v, name]
                }
              />
              <Bar isAnimationActive={false} dataKey="tokens" fill="#4d7cff" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
