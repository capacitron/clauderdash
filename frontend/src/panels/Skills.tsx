import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet, Range } from "../lib/api";
import { shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Skill = {
  skill: string;
  activations: number;
  total_tokens: number;
  sessions_count: number;
  cost_per_run: number;
};

export function Skills() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<{ skills: Skill[] } | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<{ skills: Skill[] }>(`/api/skills?range=${range}`)
      .then(setData)
      .catch(() => setData(null));
  }, [range]);

  const maxTokens = data?.skills.reduce((m, s) => Math.max(m, s.total_tokens), 0) ?? 1;

  return (
    <Panel
      kicker="07 · SKILLS"
      title="Skill usage"
      subtitle="Activations · token cost · tokens per run"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.skills.length === 0 ? (
        <EmptyState label="No slash-command skill invocations detected in this range." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.skills.slice(0, 15).map((s) => {
            const pct = Math.min(100, (s.total_tokens / (maxTokens || 1)) * 100);
            return (
              <li key={s.skill} className="rounded-lg border border-border bg-surface-2/40 p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-sm font-medium text-text">/{s.skill}</span>
                  <span className="font-mono text-xs text-text-dim">
                    {s.activations}× · {shortNum(s.total_tokens)} tok ·{" "}
                    <span className="text-text-subtle">{shortNum(s.cost_per_run)}/run</span>
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface">
                  <div
                    className={clsx(
                      "h-full rounded-full transition-all",
                      s.total_tokens > 1_000_000
                        ? "bg-accent-red"
                        : s.total_tokens > 100_000
                        ? "bg-accent-orange"
                        : "bg-accent-blue",
                    )}
                    style={{ width: `${pct}%` }}
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
