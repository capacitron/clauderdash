import { useEffect, useState } from "react";
import { apiGet, Range } from "../lib/api";
import { fmtCost, pct, projectLabel, relativeTime } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Bucket = { sessions: number; cost_usd: number };
type AbandonedRow = { id: string; cwd: string; started_at: string; cost_usd: number };

type Resp = {
  productive: Bucket;
  abandoned: Bucket;
  reverted: Bucket;
  non_git: Bucket;
  unknown: Bucket;
  yield_rate: number;
  top_abandoned: AbandonedRow[];
};

export function Yield() {
  const [range, setRange] = useState<Range>("30d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/yield?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  const analyzed = data
    ? data.productive.sessions + data.abandoned.sessions + data.reverted.sessions
    : 0;

  const tone = !data
    ? "text-text"
    : data.yield_rate >= 0.8
    ? "text-accent-green"
    : data.yield_rate >= 0.6
    ? "text-accent-cyan"
    : "text-accent-red";

  return (
    <Panel
      kicker="16 · YIELD"
      title="Session yield"
      subtitle="Did sessions produce a git commit?"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={4} />
      ) : analyzed === 0 ? (
        <EmptyState label="No analyzed sessions yet. Yield analyzer runs every 5 min." />
      ) : (
        <>
          <div className="mb-4 flex gap-6">
            <div>
              <div className={`stat-num ${tone}`}>{pct(data.yield_rate, 0)}</div>
              <div className="kicker">Yield rate</div>
            </div>
            <div>
              <div className="stat-num text-accent-red">{fmtCost(data.abandoned.cost_usd)}</div>
              <div className="kicker">Abandoned cost</div>
            </div>
          </div>

          <div className="mb-4 grid grid-cols-3 gap-2">
            {[
              { label: "Productive", bucket: data.productive, color: "text-accent-green", bar: "bg-accent-green/70" },
              { label: "Abandoned",  bucket: data.abandoned,  color: "text-accent-red",   bar: "bg-accent-red/70" },
              { label: "Reverted",   bucket: data.reverted,   color: "text-accent-orange", bar: "bg-accent-orange/70" },
            ].map(({ label, bucket, color, bar }) => (
              <div key={label} className="rounded-lg border border-border bg-surface-2/40 p-2 text-center">
                <div className={`font-mono text-sm font-bold ${color}`}>{bucket.sessions}</div>
                <div className="kicker">{label}</div>
                <div className="mt-1 font-mono text-[10px] text-text-dim">{fmtCost(bucket.cost_usd)}</div>
              </div>
            ))}
          </div>

          {data.top_abandoned.length > 0 && (
            <>
              <div className="kicker mb-1.5">Top abandoned (by cost)</div>
              <ul className="flex flex-col gap-1 overflow-y-auto scrollbar-thin" style={{ maxHeight: 160 }}>
                {data.top_abandoned.slice(0, 8).map((row) => (
                  <li key={row.id} className="flex items-center justify-between rounded border border-border/40 bg-surface-2/30 px-2 py-1.5">
                    <div className="min-w-0 flex-1 truncate">
                      <span className="font-medium text-text">{projectLabel(row.cwd)}</span>
                      <span className="ml-2 font-mono text-[9px] text-text-subtle">{relativeTime(row.started_at)}</span>
                    </div>
                    <span className="ml-2 font-mono text-xs text-accent-red">{fmtCost(row.cost_usd)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </Panel>
  );
}
