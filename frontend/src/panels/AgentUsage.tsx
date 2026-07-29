import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet, Range } from "../lib/api";
import { fmtCost, shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type AgentRow = {
  agent_type: string;
  runs: number;
  tokens: number;
  cost_usd: number;
  cost_share: number;
};

type Resp = {
  agents: AgentRow[];
  total_cost_usd: number;
  agent_cost_usd: number;
  agent_cost_share: number;
};

export function AgentUsage() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/agent-usage?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const maxCost = data?.agents.reduce((m, a) => Math.max(m, a.cost_usd), 0) ?? 1;

  return (
    <Panel
      kicker="18 · SUBAGENTS"
      title="Subagent usage"
      subtitle={
        data
          ? `${(data.agent_cost_share * 100).toFixed(0)}% of spend ran through a subagent`
          : "Cost by subagent type"
      }
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.agents.length === 0 ? (
        <EmptyState label="No subagent runs attributed in this range." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.agents.slice(0, 12).map((a) => {
            const barPct = Math.min(100, (a.cost_usd / (maxCost || 1)) * 100);
            return (
              <li key={a.agent_type} className="rounded-lg border border-border bg-surface-2/40 p-3">
                <div className="mb-1 flex items-center justify-between gap-3">
                  <span className="truncate font-mono text-sm font-medium text-text">
                    {a.agent_type}
                  </span>
                  <span className="flex-shrink-0 font-mono text-xs text-text-dim">
                    {a.runs}× · {shortNum(a.tokens)} tok ·{" "}
                    <span className="text-text-subtle">{fmtCost(a.cost_usd)}</span>
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface">
                  <div
                    className={clsx(
                      "h-full rounded-full transition-all",
                      a.cost_share > 0.15
                        ? "bg-accent-red"
                        : a.cost_share > 0.05
                        ? "bg-accent-orange"
                        : "bg-accent-blue",
                    )}
                    style={{ width: `${barPct}%` }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
