import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet } from "../lib/api";
import { pct, relativeTime } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Tier = "high" | "medium" | "low";

type Rec = {
  title: string;
  body: string;
  evidence_sql: string;
  confidence: number;
  tier: Tier;
  // Optional drift-engine fields (present only on deterministic drift recs).
  rule_ref?: string | null;
  expected?: string | null;
  actual?: string | null;
  drift_score?: number | null;
};

type Resp = {
  schema_version: number;
  generated_at: string | null;
  generator_model: string | null;
  notes: string | null;
  recommendations: Rec[];
  empty: boolean;
};

const TIER_COLOR: Record<Tier, string> = {
  high: "bg-accent-red/15 text-accent-red border-accent-red/40",
  medium: "bg-accent-orange/15 text-accent-orange border-accent-orange/40",
  low: "bg-accent-blue/15 text-accent-blue border-accent-blue/40",
};

export function SuggestionBox() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Resp>("/api/suggestions")
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const subtitle = data?.generated_at
    ? `generated ${relativeTime(data.generated_at)} · ${data.generator_model ?? "unknown"}`
    : "weekly forensic recommendations — evidence linked to queries";

  return (
    <Panel kicker="16 · SUGGESTIONS" title="Claude observed" subtitle={subtitle}>
      {err ? (
        <EmptyState label={`Load failed: ${err}`} />
      ) : !data ? (
        <Skeleton rows={3} />
      ) : data.empty ? (
        <EmptyState label="No recommendations yet. Run scripts/generate_recommendations.py or enable the weekly timer — see README." />
      ) : (
        <>
          <ul className="flex flex-1 flex-col gap-3">
            {data.recommendations.map((rec, i) => (
              <li
                key={`${rec.title}-${i}`}
                className="rounded-lg border border-border bg-surface-2/40 p-3"
              >
                <div className="mb-1.5 flex items-start justify-between gap-3">
                  <span className="text-sm font-medium text-text">{rec.title}</span>
                  <span
                    className={clsx(
                      "chip flex-shrink-0 border",
                      TIER_COLOR[rec.tier],
                    )}
                  >
                    {rec.tier}
                  </span>
                </div>
                {rec.rule_ref && (
                  <div className="mb-2 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase tracking-wider">
                    <span className="chip flex-shrink-0 border border-accent-orange/50 bg-accent-orange/10 text-accent-orange">
                      rule drift
                    </span>
                    {rec.expected && rec.actual && (
                      <span className="normal-case tracking-normal text-text-subtle">
                        expected <span className="text-text-dim">{rec.expected}</span>
                        {" · "}actual <span className="text-accent-red">{rec.actual}</span>
                      </span>
                    )}
                  </div>
                )}
                <p className="mb-2 text-xs leading-relaxed text-text-dim">
                  {rec.body}
                </p>
                <div className="flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                  <span>confidence {pct(rec.confidence, 0)}</span>
                  <details className="group">
                    <summary className="cursor-pointer select-none text-accent-blue/80 hover:text-accent-blue">
                      evidence_sql
                    </summary>
                    <pre className="mt-1.5 whitespace-pre-wrap rounded border border-border bg-surface p-2 text-[10px] normal-case tracking-normal text-text-dim">
                      {rec.evidence_sql}
                    </pre>
                  </details>
                </div>
              </li>
            ))}
          </ul>
          {data.notes && (
            <p className="mt-3 border-t border-border pt-3 text-[11px] italic leading-relaxed text-text-subtle">
              {data.notes}
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
