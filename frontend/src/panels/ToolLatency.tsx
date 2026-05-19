import { useEffect, useState } from "react";
import { apiGet, Range } from "../lib/api";
import { ms, pct, shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";
import clsx from "clsx";

type Row = {
  tool: string;
  calls: number;
  errors: number;
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
};

export function ToolLatency() {
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<{ tools: Row[] } | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<{ tools: Row[] }>(`/api/tools?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  return (
    <Panel
      kicker="03 · TOOLS"
      title="Tool latency"
      subtitle="p50 / p95 / max per tool — sorted by p95"
      action={<RangeToggle value={range} onChange={setRange} />}
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.tools.length === 0 ? (
        <EmptyState label="No tool calls in this range." />
      ) : (
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          <table className="w-full text-xs">
            <thead>
              <tr className="sticky top-0 bg-surface">
                <Th>Tool</Th>
                <Th right>Calls</Th>
                <Th right>p50</Th>
                <Th right>p95</Th>
                <Th right>Max</Th>
                <Th right>Err</Th>
              </tr>
            </thead>
            <tbody>
              {data.tools.map((t) => (
                <tr key={t.tool} className="border-t border-border/40 hover:bg-surface-2/40">
                  <Td>
                    <span className="truncate font-mono">{t.tool}</span>
                  </Td>
                  <Td right>{shortNum(t.calls)}</Td>
                  <Td right>{ms(t.p50_ms)}</Td>
                  <Td right className={clsx(t.p95_ms >= 10_000 && "text-accent-red")}>
                    {ms(t.p95_ms)}
                  </Td>
                  <Td right muted>
                    {ms(t.max_ms)}
                  </Td>
                  <Td right className={clsx(t.error_rate > 0.1 && "text-accent-orange")}>
                    {t.error_rate > 0 ? pct(t.error_rate, 0) : "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={clsx(
        "kicker py-2",
        right ? "pr-2 text-right" : "pl-2 text-left",
      )}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  muted,
  className,
}: {
  children: React.ReactNode;
  right?: boolean;
  muted?: boolean;
  className?: string;
}) {
  return (
    <td
      className={clsx(
        "py-1.5 font-mono tabular-nums",
        right ? "pr-2 text-right" : "pl-2 text-left",
        muted ? "text-text-subtle" : "text-text-dim",
        className,
      )}
    >
      {children}
    </td>
  );
}
