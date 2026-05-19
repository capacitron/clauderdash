import { useEffect, useState } from "react";
import { apiGet, Range } from "../lib/api";
import { fmtCost, pct } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type ActivityRow = {
  type: string;
  sessions: number;
  cost_usd: number;
  one_shot_rate: number;
};

type Resp = {
  breakdown: ActivityRow[];
  top_type: string | null;
};

const TYPE_LABEL: Record<string, string> = {
  feature:      "Feature build",
  debugging:    "Debugging",
  refactoring:  "Refactoring",
  exploration:  "Exploration",
  planning:     "Planning",
  delegation:   "Delegation",
  coding:       "Coding",
  testing:      "Testing",
  git:          "Git",
  build_deploy: "Build / Deploy",
  brainstorming:"Brainstorming",
  conversation: "Conversation",
  general:      "General",
  other:        "Other",
};


export function Activities() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/activities?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const maxSessions = data ? Math.max(...data.breakdown.map((r) => r.sessions), 1) : 1;

  return (
    <Panel
      kicker="12 · TAXONOMY"
      title="Work types"
      subtitle="Classifier — tool-call patterns, no LLM"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.breakdown.length === 0 ? (
        <EmptyState label="No classified sessions in this range." />
      ) : (
        <ul className="flex flex-1 flex-col gap-1.5 overflow-y-auto scrollbar-thin">
          {data.breakdown.map((row) => {
            const barPct = (row.sessions / maxSessions) * 100;
            const opacity = 0.18 + (row.sessions / maxSessions) * 0.65;
            return (
              <li key={row.type} className="rounded-lg border border-border bg-surface-2/40 p-3">
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="font-medium text-text">
                    {TYPE_LABEL[row.type] ?? row.type}
                  </span>
                  <div className="flex gap-3 font-mono text-xs text-text-dim">
                    <span>{row.sessions} sess</span>
                    <span className="text-accent-cyan">{fmtCost(row.cost_usd)}</span>
                    {row.one_shot_rate > 0 && (
                      <span className="text-accent-green">{pct(row.one_shot_rate, 0)} 1-shot</span>
                    )}
                  </div>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: barPct + "%",
                      background: `rgba(34,211,238,${opacity.toFixed(2)})`,
                    }}
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
