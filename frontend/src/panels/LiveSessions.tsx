import { useEffect, useState } from "react";
import { apiGet } from "../lib/api";
import { shortNum, projectLabel, relativeTime } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";
import { RelativeTime } from "../components/RelativeTime";

type Session = {
  id: string;
  cwd: string;
  model: string;
  started_at: string;
  last_event_at: string;
  last_user_message: string;
  total_tokens: number;
  entrypoint: string;
};

type Resp = { sessions: Session[]; cutoff_seconds: number };

export function LiveSessions({ onSelect }: { onSelect: (id: string) => void }) {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    async function load() {
      try {
        const d = await apiGet<Resp>("/api/live");
        if (!cancel) setData(d);
      } catch (e: unknown) {
        if (!cancel) setErr(e instanceof Error ? e.message : String(e));
      }
    }
    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancel = true;
      clearInterval(interval);
    };
  }, []);

  const subtitle = data
    ? `Modified in the last ${data.cutoff_seconds / 60} min`
    : "Modified in the last 5 min";

  return (
    <Panel kicker="01 · LIVE" title="Live sessions" subtitle={subtitle}>
      {err ? (
        <EmptyState label={`Error: ${err}`} />
      ) : !data ? (
        <Skeleton rows={3} />
      ) : data.sessions.length === 0 ? (
        <EmptyState label="No active sessions. Start Claude Code to see events here." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.sessions.map((s) => (
            <li
              key={s.id}
              onClick={() => onSelect(s.id)}
              className="cursor-pointer rounded-lg border border-border bg-surface-2/40 p-3 transition-colors hover:border-border-glow hover:bg-surface-2"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium text-text">
                  {s.last_user_message || "(no user message yet)"}
                </span>
                <RelativeTime ts={s.last_event_at} />
              </div>
              <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                <span>{projectLabel(s.cwd)}</span>
                <span>•</span>
                <span>{s.model?.replace(/^claude-/, "") || "?"}</span>
                <span>•</span>
                <span>{shortNum(s.total_tokens)} tok</span>
                {s.entrypoint && s.entrypoint !== "cli" && (
                  <>
                    <span>•</span>
                    <span>{s.entrypoint}</span>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
