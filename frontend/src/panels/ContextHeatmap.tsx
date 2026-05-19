import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet } from "../lib/api";
import { shortNum, pct, projectLabel } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";

type SessionCtx = {
  session_id: string;
  model: string;
  cwd: string;
  last_user_message: string;
  current_tokens: number;
  window_size: number;
  pct: number;
  status: "green" | "amber" | "red";
};

export function ContextHeatmap({ onSelect }: { onSelect: (id: string) => void }) {
  const [data, setData] = useState<{ sessions: SessionCtx[] } | null>(null);

  useEffect(() => {
    let cancel = false;
    async function load() {
      try {
        const d = await apiGet<{ sessions: SessionCtx[] }>("/api/context-heatmap");
        if (!cancel) setData(d);
      } catch {
        if (!cancel) setData({ sessions: [] });
      }
    }
    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancel = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <Panel
      kicker="10 · CONTEXT"
      title="Context window heatmap"
      subtitle="Live sessions. Compact at 40% per CLAUDE.md."
    >
      {!data ? (
        <Skeleton rows={3} />
      ) : data.sessions.length === 0 ? (
        <EmptyState label="No live sessions to monitor." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.sessions.map((s) => (
            <li
              key={s.session_id}
              onClick={() => onSelect(s.session_id)}
              className="cursor-pointer rounded-lg border border-border bg-surface-2/40 p-3 hover:border-border-glow"
            >
              <div className="mb-1 flex items-center justify-between gap-3">
                <span className="truncate text-xs text-text">
                  {s.last_user_message || projectLabel(s.cwd)}
                </span>
                <span
                  className={clsx(
                    "font-mono text-xs font-semibold tabular-nums",
                    s.status === "red"
                      ? "text-accent-red"
                      : s.status === "amber"
                      ? "text-accent-orange"
                      : "text-accent-green",
                  )}
                >
                  {pct(s.pct, 1)}
                </span>
              </div>
              <div className="mb-1 h-2 overflow-hidden rounded-full bg-surface">
                <div
                  className={clsx(
                    "h-full rounded-full",
                    s.status === "red"
                      ? "bg-accent-red"
                      : s.status === "amber"
                      ? "bg-accent-orange"
                      : "bg-accent-green",
                  )}
                  style={{ width: `${Math.min(100, s.pct * 100)}%` }}
                />
              </div>
              <div className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                {shortNum(s.current_tokens)} / {shortNum(s.window_size)} · {s.model?.replace(/^claude-/, "")}
                {s.status === "red" && " · run /compact"}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
