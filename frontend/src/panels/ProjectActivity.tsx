import { useEffect, useState } from "react";
import { apiGet, Range } from "../lib/api";
import { projectLabel, shortNum, relativeTime, fmtCost } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Project = { cwd: string; sessions: number; tokens: number; cost_usd: number; last_active: number | null };

export function ProjectActivity() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<{ projects: Project[] } | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<{ projects: Project[] }>(`/api/projects?range=${range}`)
      .then(setData)
      .catch(() => setData(null));
  }, [range]);

  const total = data?.projects.reduce((s, p) => s + p.tokens, 0) ?? 1;

  return (
    <Panel
      kicker="09 · PROJECTS"
      title="Project activity"
      subtitle="Sessions + tokens grouped by working directory"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.projects.length === 0 ? (
        <EmptyState label="No project activity in this range." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.projects.slice(0, 12).map((p) => {
            const pct = Math.min(100, (p.tokens / total) * 100);
            return (
              <li key={p.cwd} className="rounded-lg border border-border bg-surface-2/40 p-3">
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1 truncate">
                    <span className="font-medium text-text">{projectLabel(p.cwd)}</span>
                    <span className="ml-2 font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                      {p.cwd}
                    </span>
                  </div>
                  <span className="font-mono text-xs text-text-dim">
                    {p.sessions} sess · {shortNum(p.tokens)} tok
                    {p.cost_usd > 0 && (
                      <span className="ml-2 text-accent-cyan">{fmtCost(p.cost_usd)}</span>
                    )}
                  </span>
                </div>
                <div className="mb-1 h-1.5 overflow-hidden rounded-full bg-surface">
                  <div className="h-full rounded-full bg-accent-blue/70" style={{ width: pct + "%" }} />
                </div>
                {p.last_active && (
                  <div className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                    last active {relativeTime(p.last_active)}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
