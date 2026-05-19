import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet, Range } from "../lib/api";
import { relativeTime } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Fingerprint = {
  tool_name: string;
  error_preview_prefix: string;
  hits: number;
  last_seen: string;
};

type Resp = {
  range: string;
  days: number;
  fingerprints: Fingerprint[];
};

export function TwoStrike() {
  const [range, setRange] = useState<Range>("30d");
  const [data, setData] = useState<Resp | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<Resp>(`/api/two-strike?range=${range}`)
      .then(setData)
      .catch(() => setData(null));
  }, [range]);

  return (
    <Panel
      kicker="13 · TWO-STRIKE"
      title="Recurring errors"
      subtitle="Same error across ≥ 2 calls. Prefix match on first 80 chars."
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.fingerprints.length === 0 ? (
        <EmptyState label="No repeated errors in this range." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.fingerprints.map((f, i) => (
            <li
              key={`${f.tool_name}:${i}`}
              className={clsx(
                "rounded-lg border border-border bg-surface-2/40 p-3",
                f.hits >= 5 && "border-accent-red/60",
              )}
            >
              <div className="mb-1 flex items-center justify-between gap-3">
                <span className="font-mono text-xs text-text">
                  {f.tool_name}
                </span>
                <span
                  className={clsx(
                    "font-mono text-xs font-semibold tabular-nums",
                    f.hits >= 5 ? "text-accent-red" : "text-text-dim",
                  )}
                >
                  {f.hits}×
                </span>
              </div>
              <div className="truncate font-mono text-[11px] text-text-subtle">
                {f.error_preview_prefix}
              </div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                last {relativeTime(f.last_seen)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
