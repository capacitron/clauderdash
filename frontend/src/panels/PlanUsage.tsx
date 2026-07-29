import { useEffect, useState } from "react";
import clsx from "clsx";
import { relativeTime } from "../lib/format";
import type { PlanLimit, PlanUsageData } from "../lib/api";

// Plan utilisation is the one number the local JSONL cannot know — it lives on
// Anthropic's side. Deliberately NOT routed through apiGet: the provider filter
// that wraps every other call is meaningless here (the plan spans all of them).
const POLL_MS = 60_000;

const KIND_LABELS: Record<string, string> = {
  session: "Session · 5h",
  weekly_all: "Weekly · all models",
  weekly_scoped: "Weekly",
  weekly_opus: "Weekly · Opus",
};

function labelFor(l: PlanLimit): string {
  const base = KIND_LABELS[l.kind] ?? l.kind.replace(/_/g, " ");
  return l.scope_label ? `${base} · ${l.scope_label}` : base;
}

function toneFor(percent: number, severity: string): string {
  if (severity === "critical" || percent >= 90) return "text-accent-red";
  if (severity === "warning" || percent >= 70) return "text-accent-orange";
  return "text-accent-green";
}

function barFor(percent: number, severity: string): string {
  if (severity === "critical" || percent >= 90) return "bg-accent-red";
  if (severity === "warning" || percent >= 70) return "bg-accent-orange";
  return "bg-accent-cyan";
}

export function PlanUsage() {
  const [data, setData] = useState<PlanUsageData | null>(null);

  useEffect(() => {
    const load = () =>
      fetch("/api/plan-usage", { headers: { Accept: "application/json" } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then(setData)
        .catch(() => setData({ available: false, reason: "Backend unreachable", limits: [] }));
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (!data) {
    return <div className="mb-3 h-14 animate-pulse rounded-lg border border-border bg-surface-2/40" />;
  }

  if (!data.available) {
    return (
      <div className="mb-3 rounded-lg border border-border bg-surface-2/40 px-4 py-2.5">
        <span className="kicker">Anthropic plan usage</span>
        <span className="ml-3 font-mono text-xs text-text-dim">{data.reason}</span>
      </div>
    );
  }

  const extra = data.extra_usage;

  return (
    <div className="mb-3 rounded-lg border border-border bg-surface-2/40 px-4 py-3">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="kicker">Anthropic plan usage</span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
          {relativeTime(data.fetched_at)}
        </span>
      </div>
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        {data.limits.map((l) => (
          <div key={`${l.kind}-${l.scope_label ?? ""}`}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-xs text-text-dim">{labelFor(l)}</span>
              <span
                className={clsx(
                  "font-mono text-sm font-semibold tabular-nums",
                  toneFor(l.percent, l.severity),
                )}
              >
                {Math.round(l.percent)}%
              </span>
            </div>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-3">
              <div
                className={clsx("h-full rounded-full transition-all", barFor(l.percent, l.severity))}
                style={{ width: `${Math.min(100, Math.max(0, l.percent))}%` }}
              />
            </div>
            <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-text-subtle">
              resets {relativeTime(l.resets_at)}
            </div>
          </div>
        ))}
        {extra?.enabled && (
          <div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-xs text-text-dim">Extra usage credits</span>
              <span className="font-mono text-sm font-semibold tabular-nums text-text">
                {extra.utilization != null ? `${Math.round(extra.utilization)}%` : "—"}
              </span>
            </div>
            <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-text-subtle">
              {extra.used_credits ?? 0} of {extra.monthly_limit ?? "—"}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
