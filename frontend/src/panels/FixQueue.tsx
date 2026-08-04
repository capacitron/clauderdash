import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet, apiPost } from "../lib/api";
import { shortNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Status = "open" | "snoozed" | "accepted" | "fixed";

type Finding = {
  key: string;
  source: "detector" | "signal" | "drift" | "llm";
  gauge: string;
  severity: "high" | "medium" | "low";
  title: string;
  detail: string | null;
  est_tokens: number | null; // null = cost not derivable, NOT zero
  fix_kind: "shell" | "prompt" | "manual";
  fix_label: string | null;
  fix_text: string | null;
  auto_run: boolean;
  first_seen: string;
  last_seen: string;
  status: Status;
  note: string | null;
  snooze_until: string | null;
};

type UndoableAction = {
  id: number;
  finding_key: string;
  command: string;
  status: string;
  ran_at: string;
};

type Resp = {
  findings: Finding[];
  total_tokens: number;
  health_score: number;
  health_grade: "A" | "B" | "C" | "D" | "F";
  checks_failing: number;
  checks_total: number;
  undoable: UndoableAction[];
  last_scanned: string | null;
};

const GRADE_COLOR: Record<string, string> = {
  A: "text-accent-green",
  B: "text-accent-green",
  C: "text-accent-cyan",
  D: "text-accent-orange",
  F: "text-accent-red",
};

const SEVERITY_ROW: Record<string, string> = {
  high: "border-accent-red/30 bg-accent-red/5",
  medium: "border-accent-orange/30 bg-accent-orange/5",
  low: "border-border bg-surface-2/30",
};

const SEVERITY_CHIP: Record<string, string> = {
  high: "bg-accent-red/10 text-accent-red",
  medium: "bg-accent-orange/10 text-accent-orange",
  low: "bg-surface text-text-dim",
};

const SOURCE_LABEL: Record<string, string> = {
  detector: "scan",
  signal: "live",
  drift: "drift",
  llm: "claude",
};

const TITLE_LABEL: Record<string, string> = {
  junk_reads: "Junk directory reads",
  duplicate_reads: "Duplicate file reads",
  low_read_edit_ratio: "Low read/edit ratio",
  unused_mcp: "Unused MCP servers",
  bloated_claude_md: "Bloated CLAUDE.md",
  bash_bloat: "Bash output limit",
  ghost_agents: "Ghost agents",
  ghost_skills: "Ghost skills",
};

const SNOOZE_DAYS = 7;

function label(f: Finding): string {
  return TITLE_LABEL[f.title] ?? f.title;
}

/** What the operator hands to Claude for a `prompt` fix. The evidence is baked
 *  in so the pasted prompt stands alone — a generic "clean up duplicate reads"
 *  would make them go dig the numbers out again. */
function promptText(f: Finding): string {
  return [
    f.detail ?? label(f),
    "",
    f.fix_text ?? "",
    "",
    "Show me the diff before applying anything.",
  ]
    .filter((s, i, a) => !(s === "" && a[i - 1] === ""))
    .join("\n");
}

export function FixQueue() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    apiGet<Resp>("/api/fix-queue")
      .then((d) => {
        setData(d);
        setErr(null);
      })
      .catch((e: unknown) =>
        setErr(e instanceof Error ? e.message : String(e)),
      );
  }, []);

  useEffect(load, [load]);

  const say = (key: string, msg: string) => {
    setFlash((f) => ({ ...f, [key]: msg }));
    window.setTimeout(() => setFlash((f) => ({ ...f, [key]: "" })), 4000);
  };

  const act = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try {
      await fn();
      say(key, ok);
      load();
    } catch (e: unknown) {
      say(key, e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const setState = (f: Finding, status: Status) => {
    const snooze_until =
      status === "snoozed"
        ? new Date(Date.now() + SNOOZE_DAYS * 86_400_000).toISOString()
        : null;
    return act(
      f.key,
      () =>
        apiPost(`/api/fix/state/${f.key}`, {
          status,
          note: null,
          snooze_until,
        }),
      status === "snoozed" ? `snoozed ${SNOOZE_DAYS}d` : status,
    );
  };

  const copy = (f: Finding, text: string) =>
    act(f.key, () => navigator.clipboard.writeText(text), "copied");

  const subtitle = data
    ? `${data.checks_total - data.checks_failing}/${data.checks_total} checks passing · ${data.findings.length} open`
    : "ranked by severity, then measured cost";

  return (
    <Panel kicker="FIX QUEUE" title="What to fix" subtitle={subtitle}>
      {err ? (
        <EmptyState label={`Load failed: ${err}`} />
      ) : !data ? (
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
            {data.total_tokens > 0 && (
              <div>
                <div className="stat-num text-accent-cyan">
                  ~{shortNum(data.total_tokens)}
                </div>
                <div className="kicker">Tokens/wk measured</div>
              </div>
            )}
          </div>

          {data.findings.length === 0 ? (
            <EmptyState label="Nothing open. Setup is clean." />
          ) : (
            <ul
              className="flex flex-col gap-2 overflow-y-auto scrollbar-thin"
              style={{ maxHeight: 420 }}
            >
              {data.findings.map((f) => {
                const open = expanded === f.key;
                const dimmed =
                  f.status === "accepted" || f.status === "snoozed";
                return (
                  <li
                    key={f.key}
                    className={clsx(
                      "rounded-lg border p-3 transition-colors",
                      SEVERITY_ROW[f.severity],
                      dimmed && "opacity-50",
                    )}
                  >
                    <div
                      className="flex cursor-pointer items-center justify-between gap-2"
                      onClick={() => setExpanded(open ? null : f.key)}
                    >
                      <span className="flex items-center gap-2 font-medium text-text">
                        {label(f)}
                        <span className="chip bg-surface text-[9px] uppercase text-text-subtle">
                          {SOURCE_LABEL[f.source] ?? f.source}
                        </span>
                        {f.status !== "open" && (
                          <span className="chip bg-surface text-[9px] uppercase text-text-dim">
                            {f.status}
                          </span>
                        )}
                      </span>
                      <div className="flex items-center gap-2">
                        {f.est_tokens !== null && f.est_tokens > 0 && (
                          <span className="font-mono text-[10px] text-text-dim">
                            ~{shortNum(f.est_tokens)} tok
                          </span>
                        )}
                        <span
                          className={clsx(
                            "chip text-[10px] uppercase",
                            SEVERITY_CHIP[f.severity],
                          )}
                        >
                          {f.severity}
                        </span>
                      </div>
                    </div>

                    {open && (
                      <div className="mt-2 space-y-2">
                        <p className="text-xs text-text-dim">{f.detail}</p>

                        {f.est_tokens === null && (
                          // Say so rather than showing nothing. An absent
                          // number reads as "free", and these are real
                          // problems whose cost this data cannot price.
                          <p className="font-mono text-[10px] text-text-subtle">
                            cost not measurable from available telemetry
                          </p>
                        )}

                        {f.fix_text && (
                          <div>
                            {f.fix_label && (
                              <div className="kicker mb-1">{f.fix_label}</div>
                            )}
                            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-surface p-2 font-mono text-[10px] text-accent-cyan">
                              {f.fix_text}
                            </pre>
                          </div>
                        )}

                        <div className="flex flex-wrap items-center gap-2">
                          {f.auto_run && (
                            <button
                              disabled={busy === f.key}
                              onClick={() =>
                                act(
                                  f.key,
                                  () => apiPost(`/api/fix/run/${f.key}`),
                                  "applied",
                                )
                              }
                              className="chip bg-accent-green/15 text-[10px] uppercase text-accent-green hover:bg-accent-green/25 disabled:opacity-40"
                            >
                              Run fix
                            </button>
                          )}
                          {f.fix_text && (
                            <button
                              disabled={busy === f.key}
                              onClick={() =>
                                copy(
                                  f,
                                  f.fix_kind === "prompt"
                                    ? promptText(f)
                                    : f.fix_text!,
                                )
                              }
                              className="chip bg-surface text-[10px] uppercase text-text-dim hover:text-text disabled:opacity-40"
                            >
                              {f.fix_kind === "prompt"
                                ? "Copy prompt"
                                : "Copy command"}
                            </button>
                          )}
                          {f.status === "open" ? (
                            <>
                              <button
                                disabled={busy === f.key}
                                onClick={() => setState(f, "accepted")}
                                className="chip bg-surface text-[10px] uppercase text-text-dim hover:text-text disabled:opacity-40"
                                title="Deliberate — stop counting this against the grade"
                              >
                                Accept
                              </button>
                              <button
                                disabled={busy === f.key}
                                onClick={() => setState(f, "snoozed")}
                                className="chip bg-surface text-[10px] uppercase text-text-dim hover:text-text disabled:opacity-40"
                              >
                                Snooze {SNOOZE_DAYS}d
                              </button>
                            </>
                          ) : (
                            <button
                              disabled={busy === f.key}
                              onClick={() => setState(f, "open")}
                              className="chip bg-surface text-[10px] uppercase text-text-dim hover:text-text disabled:opacity-40"
                            >
                              Reopen
                            </button>
                          )}
                          {flash[f.key] && (
                            <span className="font-mono text-[10px] text-accent-cyan">
                              {flash[f.key]}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {data.undoable.length > 0 && (
            <div className="mt-4 border-t border-border pt-3">
              <div className="kicker mb-2">Applied — undo available</div>
              <ul className="flex flex-col gap-1">
                {data.undoable.map((a) => (
                  <li
                    key={a.id}
                    className="flex items-center justify-between gap-2 text-[11px]"
                  >
                    <span className="font-mono text-text-dim">
                      {TITLE_LABEL[a.command] ?? a.command}
                    </span>
                    <div className="flex items-center gap-2">
                      {flash[`undo-${a.id}`] && (
                        <span className="font-mono text-[10px] text-accent-cyan">
                          {flash[`undo-${a.id}`]}
                        </span>
                      )}
                      <button
                        disabled={busy === `undo-${a.id}`}
                        onClick={() =>
                          act(
                            `undo-${a.id}`,
                            () => apiPost(`/api/fix/undo/${a.id}`),
                            "undone",
                          )
                        }
                        className="chip bg-surface text-[10px] uppercase text-text-dim hover:text-text disabled:opacity-40"
                      >
                        Undo
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
