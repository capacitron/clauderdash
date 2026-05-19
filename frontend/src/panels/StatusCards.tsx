import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet } from "../lib/api";
import { shortNum, pct, projectLabel } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";

type Resp = {
  live_sessions: number;
  tokens_today: number;
  avg_ctx_pct: number;
  top_project: string | null;
};

export function StatusCards() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    const load = () => {
      apiGet<Resp>("/api/status-cards")
        .then((d) => {
          if (!cancel) {
            setData(d);
            setErr(null);
          }
        })
        .catch((e: unknown) => {
          if (!cancel) setErr(e instanceof Error ? e.message : String(e));
        });
    };
    load();
    const id = setInterval(load, 5000);
    return () => {
      cancel = true;
      clearInterval(id);
    };
  }, []);

  const ctxClass =
    data && data.avg_ctx_pct >= 0.4
      ? "text-accent-red"
      : data && data.avg_ctx_pct >= 0.3
      ? "text-accent-orange"
      : "text-text";

  return (
    <Panel
      kicker="12 · STATUS"
      title="Right now"
      subtitle="Live aggregate across active sessions"
    >
      {err ? (
        <div className="text-xs text-accent-red">Error: {err}</div>
      ) : !data ? (
        <Skeleton rows={2} />
      ) : (
        <div className="grid flex-1 grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Live sessions" value={String(data.live_sessions)} />
          <Tile label="Tokens today" value={shortNum(data.tokens_today)} />
          <Tile
            label="Avg context"
            value={pct(data.avg_ctx_pct, 1)}
            className={ctxClass}
          />
          <Tile
            label="Top project"
            value={data.top_project ? projectLabel(data.top_project) : "—"}
            mono={false}
          />
        </div>
      )}
    </Panel>
  );
}

function Tile({
  label,
  value,
  className,
  mono = true,
}: {
  label: string;
  value: string;
  className?: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-2/40 p-3">
      <div className="kicker mb-1">{label}</div>
      <div
        className={clsx(
          mono ? "font-mono" : "",
          "truncate text-xl font-semibold tabular-nums",
          className ?? "text-text",
        )}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}
