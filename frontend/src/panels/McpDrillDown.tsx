import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet, Range } from "../lib/api";
import { ms, pct, shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { RangeToggle } from "../components/RangeToggle";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Tool = {
  tool: string;
  calls: number;
  errors: number;
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
};
type Server = Tool & { server: string; avg_ms: number; tools: Tool[] };

export function McpDrillDown() {
  // MCP calls are sparse — default to 30d so the panel shows data on first load.
  const [range, setRange] = useState<Range>("30d");
  const [data, setData] = useState<{ servers: Server[] } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    apiGet<{ servers: Server[] }>(`/api/mcp?range=${range}`).then(setData).catch(() => setData(null));
  }, [range]);

  return (
    <Panel
      kicker="04 · MCP"
      title="MCP"
      subtitle="Server drill-down — cost vs latency"
      action={<RangeToggle value={range} onChange={setRange} />}
      centerpiece
      className="xl:col-span-2"
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : data.servers.length === 0 ? (
        <EmptyState label="No MCP calls detected in this range." />
      ) : (
        <div className="flex flex-col gap-2 overflow-y-auto scrollbar-thin">
          {data.servers.map((s) => {
            const open = expanded === s.server;
            const tone =
              s.p95_ms >= 10_000
                ? "text-accent-red"
                : s.p95_ms < 500
                ? "text-accent-green"
                : "text-text";
            return (
              <div
                key={s.server}
                className={clsx(
                  "overflow-hidden rounded-lg border transition-colors",
                  open
                    ? "border-accent-purple/50 bg-accent-purple/5"
                    : "border-border bg-surface-2/40 hover:border-border-glow",
                )}
              >
                <button
                  onClick={() => setExpanded(open ? null : s.server)}
                  className="flex w-full items-center gap-4 p-3 text-left"
                >
                  <span className="flex-1 truncate font-mono text-sm font-semibold text-text">
                    {s.server}
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                    {shortNum(s.calls)} calls
                  </span>
                  <span className={clsx("font-mono text-xs tabular-nums", tone)}>
                    p95 {ms(s.p95_ms)}
                  </span>
                  <span className="font-mono text-xs tabular-nums text-text-dim">
                    avg {ms(s.avg_ms)}
                  </span>
                  {s.error_rate > 0 && (
                    <span className="font-mono text-xs tabular-nums text-accent-orange">
                      {pct(s.error_rate, 0)} err
                    </span>
                  )}
                  <span className="text-text-subtle">{open ? "−" : "+"}</span>
                </button>
                {open && (
                  <div className="border-t border-border/60 bg-bg/30 p-3">
                    <table className="w-full text-xs">
                      <thead>
                        <tr>
                          <Th>Tool</Th>
                          <Th right>Calls</Th>
                          <Th right>p50</Th>
                          <Th right>p95</Th>
                          <Th right>Max</Th>
                          <Th right>Err</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.tools.map((t) => (
                          <tr key={t.tool} className="border-t border-border/30">
                            <Td>
                              <span className="font-mono text-[11px]">{t.tool}</span>
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
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={clsx("kicker py-1.5", right ? "pr-2 text-right" : "pl-2 text-left")}>
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
        "py-1 font-mono tabular-nums",
        right ? "pr-2 text-right" : "pl-2 text-left",
        muted ? "text-text-subtle" : "text-text-dim",
        className,
      )}
    >
      {children}
    </td>
  );
}
