import { useEffect, useState } from "react";
import { apiGet } from "../lib/api";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Severity = "high" | "medium" | "low" | "ok";

type Rec = {
  id: string;
  title: string;
  body: string;
  action: string | null;
  severity: Severity;
};

type Resp = {
  recommendations: Rec[];
  count: number;
  actionable: number;
};

const SEVERITY_STYLES: Record<Severity, { bar: string; chip: string; label: string }> = {
  high:   { bar: "bg-accent-red",    chip: "bg-accent-red/15 text-accent-red border-accent-red/35",       label: "urgent"  },
  medium: { bar: "bg-accent-orange", chip: "bg-accent-orange/15 text-accent-orange border-accent-orange/35", label: "worth fixing" },
  low:    { bar: "bg-accent-cyan/60", chip: "bg-accent-cyan/10 text-accent-cyan border-accent-cyan/30",   label: "note"    },
  ok:     { bar: "bg-accent-green",  chip: "bg-accent-green/15 text-accent-green border-accent-green/35",  label: "good"    },
};

export function Intelligence() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Resp>("/api/intelligence")
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const subtitle = data
    ? data.actionable > 0
      ? `${data.actionable} item${data.actionable !== 1 ? "s" : ""} need your attention`
      : "nothing urgent right now"
    : "live · no external calls · fresh on load";

  return (
    <Panel kicker="06 · SIGNAL" title="Signal" subtitle={subtitle}>
      {err ? (
        <EmptyState label={`Load failed: ${err}`} />
      ) : !data ? (
        <Skeleton rows={3} />
      ) : data.recommendations.length === 0 ? (
        <EmptyState label="All clear. No issues detected in current data." />
      ) : (
        <ul className="flex flex-1 flex-col gap-2.5">
          {data.recommendations.map((rec) => {
            const s = SEVERITY_STYLES[rec.severity];
            return (
              <li
                key={rec.id}
                className="rounded-xl border border-border bg-surface-2/50 overflow-hidden"
              >
                <div className={`h-0.5 w-full ${s.bar}`} />
                <div className="p-3.5">
                  <div className="mb-1.5 flex items-start justify-between gap-3">
                    <span className="font-display text-sm font-semibold text-text leading-snug">
                      {rec.title}
                    </span>
                    <span className={`chip flex-shrink-0 border ${s.chip}`}>
                      {s.label}
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-text-dim mb-0">
                    {rec.body}
                  </p>
                  {rec.action && (
                    <div className="mt-2 flex items-center gap-1.5">
                      <span className="text-accent-cyan/60 text-xs">→</span>
                      <span className="font-mono text-[10px] text-accent-cyan tracking-wide">
                        {rec.action}
                      </span>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
