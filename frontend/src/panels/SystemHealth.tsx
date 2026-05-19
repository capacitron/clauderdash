import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet } from "../lib/api";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";

type SystemHealthData = {
  available: boolean;
  timestamp?: string;
  cpu: { percent: number; status: "green" | "amber" | "red" };
  memory: { percent: number; available_mb: number; status: "green" | "amber" | "red" };
  disk: { percent: number; status: "green" | "amber" | "red" };
  load: number;
  process_count: number;
};

type Hook = {
  name: string;
  status: "enabled" | "disabled";
  last_ran_at: string | null;
};

type HookStatusData = {
  hooks: Hook[];
};

type Process = {
  pid: number;
  name: string;
  rss_mb: number;
  cpu_percent: number;
  uptime_hours: number;
};

type ProcessMonitorData = {
  available: boolean;
  processes: Process[];
};

function StatusBar({ percent, status }: { percent: number; status: string }) {
  const colorClass = {
    green: "bg-accent-green/70",
    amber: "bg-accent-orange/70",
    red:   "bg-accent-red/70",
  }[status] || "bg-text-subtle/70";

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 overflow-hidden rounded bg-surface-2/50 p-0.5">
        <div
          className={clsx("h-1.5 rounded-sm transition-all", colorClass)}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
      <span className="text-xs font-mono text-text-dim w-10 text-right">
        {percent.toFixed(0)}%
      </span>
    </div>
  );
}

export function SystemHealth() {
  const [health, setHealth] = useState<SystemHealthData | null>(null);
  const [hooks, setHooks] = useState<HookStatusData | null>(null);
  const [processes, setProcesses] = useState<ProcessMonitorData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;

    async function load() {
      try {
        const [h, hk, p] = await Promise.all([
          apiGet<SystemHealthData>("/api/system-health"),
          apiGet<HookStatusData>("/api/hook-status"),
          apiGet<ProcessMonitorData>("/api/process-monitor"),
        ]);
        if (!cancel) {
          setHealth(h);
          setHooks(hk);
          setProcesses(p);
          setErr(null);
        }
      } catch (e: unknown) {
        if (!cancel) {
          setErr(e instanceof Error ? e.message : String(e));
        }
      }
    }

    load();
    const interval = setInterval(load, 10000); // 10-second poll
    return () => {
      cancel = true;
      clearInterval(interval);
    };
  }, []);

  // Determine if any critical alert should show
  const criticalAlert =
    health?.available &&
    (health.cpu.status === "red" ||
      health.memory.status === "red" ||
      health.disk.status === "red");

  return (
    <>
      {criticalAlert && (
        <div className="col-span-4 mb-4 rounded-lg border border-accent-red/50 bg-accent-red/10 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm text-accent-red font-medium">
              SYSTEM PRESSURE:{" "}
              {health!.cpu.status === "red" && `CPU ${health!.cpu.percent.toFixed(0)}%`}
              {health!.memory.status === "red" && ` Memory ${health!.memory.percent.toFixed(0)}%`}
              {health!.disk.status === "red" && ` Disk ${health!.disk.percent.toFixed(0)}%`}
            </div>
          </div>
        </div>
      )}
      <Panel kicker="00 · VITALS" title="System" subtitle="Real-time CPU, RAM, disk">
        {err ? (
          <EmptyState label={`Error: ${err}`} />
        ) : !health || !hooks || !processes ? (
          <Skeleton rows={6} />
        ) : !health.available ? (
          <EmptyState label="System metrics not available yet. Starting collector..." />
        ) : (
          <div className="space-y-4">
            {/* CPU, RAM, Disk bars */}
            <div className="space-y-2">
              <div>
                <div className="mb-1 text-xs font-medium text-text-dim">CPU</div>
                <StatusBar percent={health.cpu.percent} status={health.cpu.status} />
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-text-dim">
                  Memory ({health.memory.available_mb}MB free)
                </div>
                <StatusBar percent={health.memory.percent} status={health.memory.status} />
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-text-dim">Disk</div>
                <StatusBar percent={health.disk.percent} status={health.disk.status} />
              </div>
              <div className="text-xs text-text-dim">
                Load: {health.load} · Processes: {health.process_count}
              </div>
            </div>

            {/* Hook status */}
            <div className="border-t border-border/30 pt-3">
              <div className="mb-2 text-xs font-medium text-text-dim">Hook Status</div>
              <div className="space-y-1 text-[10px] font-mono">
                {hooks.hooks.slice(0, 5).map((h) => (
                  <div key={h.name} className="flex items-center justify-between">
                    <span>{h.name}</span>
                    <span className={h.status === "enabled" ? "text-accent-green" : "text-text-subtle"}>
                      {h.status === "enabled" ? "✓" : "✗"} {h.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Top processes */}
            <div className="border-t border-border/30 pt-3">
              <div className="mb-2 text-xs font-medium text-text-dim">Top Consumers</div>
              <div className="space-y-1 text-[10px] font-mono">
                {processes.available && processes.processes.length > 0 ? (
                  processes.processes.map((p) => (
                    <div key={p.pid} className="flex items-center justify-between">
                      <span className="truncate">{p.name}</span>
                      <span className="text-text-dim">
                        {p.rss_mb}MB · {p.cpu_percent.toFixed(0)}%
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="text-text-dim">No process data</div>
                )}
              </div>
            </div>
          </div>
        )}
      </Panel>
    </>
  );
}
