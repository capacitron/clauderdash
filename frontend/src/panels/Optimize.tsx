import { useEffect, useState } from "react";
import { apiGet } from "../lib/api";
import { shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";
import clsx from "clsx";

type Finding = {
  pattern: string;
  severity: "high" | "medium" | "low";
  detail: string;
  tokens_saved: number | null; // null = cost not derivable from the data we hold
  fix_label: string | null;
  fix_text: string | null;
  detected_at: string;
};

type Resp = {
  findings: Finding[];
  total_tokens_saved: number;
  health_score: number;
  health_grade: "A" | "B" | "C" | "D" | "F";
  last_scanned: string | null;
};

const SEVERITY_COLOR = {
  high: "text-accent-red border-accent-red/30 bg-accent-red/5",
  medium: "text-accent-orange border-accent-orange/30 bg-accent-orange/5",
  low: "text-text-dim border-border bg-surface-2/30",
};

const GRADE_COLOR: Record<string, string> = {
  A: "text-accent-green",
  B: "text-accent-green",
  C: "text-accent-cyan",
  D: "text-accent-orange",
  F: "text-accent-red",
};

const PATTERN_LABEL: Record<string, string> = {
  junk_reads: "Junk directory reads",
  duplicate_reads: "Duplicate file reads",
  low_read_edit_ratio: "Low read/edit ratio",
  unused_mcp: "Unused MCP servers",
  bloated_claude_md: "Bloated CLAUDE.md",
  bash_bloat: "Bash output limit",
  ghost_agents: "Ghost agents",
  ghost_skills: "Ghost skills",
};

export function Optimize() {
  const [data, setData] = useState<Resp | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Resp>("/api/optimize")
      .then(setData)
      .catch(() => setData(null));
  }, []);

  return (
    <Panel
      kicker="OPTIMIZE"
      title="Waste detection"
      subtitle="Config health · token waste patterns"
    >
      {!data ? (
        <Skeleton rows={3} />
      ) : (
        <>
          <div className="mb-4 flex items-center gap-6">
            <div>
              <div
                className={clsx(
                  "stat-num",
                  GRADE_COLOR[data.health_grade] ?? "text-text",
                )}
              >
                {data.health_grade}
              </div>
              <div className="kicker">Health grade</div>
            </div>
            <div>
              <div className="stat-num text-text">{data.health_score}/100</div>
              <div className="kicker">Score</div>
            </div>
            {data.total_tokens_saved > 0 && (
              <div>
                <div className="stat-num text-accent-cyan">
                  ~{shortNum(data.total_tokens_saved)}
                </div>
                <div className="kicker">Tokens/save</div>
              </div>
            )}
          </div>

          {data.findings.length === 0 ? (
            <EmptyState label="Nothing to fix. Setup is clean." />
          ) : (
            <ul
              className="flex flex-col gap-2 overflow-y-auto scrollbar-thin"
              style={{ maxHeight: 340 }}
            >
              {data.findings.map((f) => (
                <li
                  key={f.pattern}
                  className={clsx(
                    "cursor-pointer rounded-lg border p-3 transition-colors",
                    SEVERITY_COLOR[f.severity],
                  )}
                  onClick={() =>
                    setExpanded(expanded === f.pattern ? null : f.pattern)
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-text">
                      {PATTERN_LABEL[f.pattern] ?? f.pattern}
                    </span>
                    <div className="flex items-center gap-2">
                      {f.tokens_saved !== null && f.tokens_saved > 0 && (
                        <span className="font-mono text-[10px] text-text-dim">
                          ~{shortNum(f.tokens_saved)} tok saved
                        </span>
                      )}
                      <span
                        className={clsx("chip text-[10px] uppercase", {
                          "bg-accent-red/10 text-accent-red":
                            f.severity === "high",
                          "bg-accent-orange/10 text-accent-orange":
                            f.severity === "medium",
                          "bg-surface text-text-dim": f.severity === "low",
                        })}
                      >
                        {f.severity}
                      </span>
                    </div>
                  </div>

                  {expanded === f.pattern && (
                    <div className="mt-2 space-y-2">
                      <p className="text-xs text-text-dim">{f.detail}</p>
                      {f.fix_label && f.fix_text && (
                        <div>
                          <div className="kicker mb-1">{f.fix_label}</div>
                          <pre className="overflow-x-auto rounded bg-surface p-2 font-mono text-[10px] text-accent-cyan whitespace-pre-wrap">
                            {f.fix_text}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}
