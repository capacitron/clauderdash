import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { fmtCost, projectLabel } from "../lib/format";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Session = {
  id: string;
  started_at: string;
  cwd: string;
  last_user_message: string;
  outcome: string;
  cost_usd: number;
};

type BrowseResp = {
  total: number;
  sessions: Session[];
  projects: string[];
  limit: number;
  offset: number;
};

const OUTCOME_DOT: Record<string, string> = {
  ok: "bg-green-500",
  errored: "bg-red-500",
  rate_limited: "bg-amber-400",
  truncated: "bg-amber-400",
  unfinished: "bg-neutral-600",
};

function fmtTime(iso: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function fmtDayHeader(iso: string): string {
  if (!iso) return "Unknown";
  return new Date(iso).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

type Row =
  | { kind: "header"; label: string; key: string }
  | { kind: "session"; session: Session };

function buildRows(sessions: Session[]): Row[] {
  const rows: Row[] = [];
  let lastDay = "";
  for (const s of sessions) {
    const day = s.started_at?.slice(0, 10) ?? "unknown";
    if (day !== lastDay) {
      rows.push({ kind: "header", label: fmtDayHeader(s.started_at), key: `h-${day}` });
      lastDay = day;
    }
    rows.push({ kind: "session", session: s });
  }
  return rows;
}

const LIMIT = 100;

export function SessionBrowser({ onSelect }: { onSelect: (id: string) => void }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [projects, setProjects] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [project, setProject] = useState("");
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<{ id: string; content: string } | null>(null);
  const summaryCache = useRef(new Map<string, string>());
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const qTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedQ, setDebouncedQ] = useState("");

  useEffect(() => {
    if (qTimer.current) clearTimeout(qTimer.current);
    qTimer.current = setTimeout(() => setDebouncedQ(q), 300);
  }, [q]);

  const fetchPage = useCallback(
    async (query: string, proj: string, off: number, append: boolean) => {
      setLoading(true);
      setErr(null);
      try {
        const p = new URLSearchParams();
        if (query) p.set("q", query);
        if (proj) p.set("project", proj);
        p.set("limit", String(LIMIT));
        p.set("offset", String(off));
        const res = await fetch(`/api/sessions-browse?${p}`, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const d: BrowseResp = await res.json();
        setSessions((prev) => (append ? [...prev, ...d.sessions] : d.sessions));
        setTotal(d.total);
        setProjects(d.projects);
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    setOffset(0);
    fetchPage(debouncedQ, project, 0, false);
  }, [debouncedQ, project, fetchPage]);

  function loadMore() {
    const next = offset + LIMIT;
    setOffset(next);
    fetchPage(debouncedQ, project, next, true);
  }

  function handleMouseEnter(s: Session) {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(async () => {
      if (summaryCache.current.has(s.id)) {
        const c = summaryCache.current.get(s.id)!;
        if (c) setPreview({ id: s.id, content: c });
        return;
      }
      try {
        const res = await fetch(`/api/session-summary/${s.id}`);
        const j = await res.json();
        const content: string = j.found ? j.content : "";
        summaryCache.current.set(s.id, content);
        if (content) setPreview({ id: s.id, content });
      } catch {
        summaryCache.current.set(s.id, "");
      }
    }, 350);
  }

  function handleMouseLeave() {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    setPreview(null);
  }

  const rows = buildRows(sessions);
  const shown = sessions.length;

  return (
    <div className="panel flex flex-col">
      {/* Header */}
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="kicker mb-1">00 · SESSIONS</div>
          <h2 className="text-base font-semibold text-text">Session Browser</h2>
          <p className="mt-0.5 text-xs text-text-dim">
            {loading && sessions.length === 0
              ? "Loading…"
              : `${total.toLocaleString()} sessions · showing ${shown}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            placeholder="Search title or project…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-52 rounded border border-border bg-surface-2 px-2 py-1 font-mono text-xs text-text placeholder-text-subtle focus:border-accent-cyan focus:outline-none"
          />
          {projects.length > 0 && (
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className="rounded border border-border bg-surface-2 px-2 py-1 font-mono text-xs text-text focus:border-accent-cyan focus:outline-none"
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p} value={p}>
                  {projectLabel(p)}
                </option>
              ))}
            </select>
          )}
        </div>
      </header>

      {/* Content */}
      {err ? (
        <EmptyState label={`Error: ${err}`} />
      ) : loading && sessions.length === 0 ? (
        <Skeleton rows={8} />
      ) : sessions.length === 0 ? (
        <EmptyState label="No sessions found." />
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full border-collapse font-mono text-xs">
            <thead>
              <tr>
                <th className="w-14 pb-2 text-left font-normal uppercase tracking-wider text-text-subtle">
                  Time
                </th>
                <th className="w-32 pb-2 text-left font-normal uppercase tracking-wider text-text-subtle">
                  Project
                </th>
                <th className="pb-2 text-left font-normal uppercase tracking-wider text-text-subtle">
                  Title
                </th>
                <th className="w-20 pb-2 text-left font-normal uppercase tracking-wider text-text-subtle">
                  ID
                </th>
                <th className="w-16 pb-2 text-right font-normal uppercase tracking-wider text-text-subtle">
                  Cost
                </th>
                <th className="w-5 pb-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) =>
                row.kind === "header" ? (
                  <tr key={row.key}>
                    <td
                      colSpan={6}
                      className="border-t border-border bg-surface/60 px-0 pb-1.5 pt-3 text-[10px] uppercase tracking-widest text-text-subtle"
                    >
                      {row.label}
                    </td>
                  </tr>
                ) : (
                  <tr
                    key={row.session.id}
                    onClick={() => onSelect(row.session.id)}
                    onMouseEnter={() => handleMouseEnter(row.session)}
                    onMouseLeave={handleMouseLeave}
                    className="cursor-pointer border-b border-border/30 transition-colors hover:bg-surface-2/60"
                  >
                    <td className="py-1.5 pr-3 tabular-nums text-text-subtle">
                      {fmtTime(row.session.started_at)}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span className="inline-block max-w-[120px] truncate rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-dim">
                        {projectLabel(row.session.cwd)}
                      </span>
                    </td>
                    <td className="max-w-0 truncate py-1.5 pr-3 text-text">
                      {row.session.last_user_message || "(no title)"}
                    </td>
                    <td className="py-1.5 pr-3 text-text-subtle">
                      {row.session.id.slice(0, 8)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-text-subtle">
                      {row.session.cost_usd > 0 ? fmtCost(row.session.cost_usd) : "—"}
                    </td>
                    <td className="py-1.5 text-center">
                      <span
                        className={clsx(
                          "inline-block h-1.5 w-1.5 rounded-full",
                          OUTCOME_DOT[row.session.outcome] ?? "bg-neutral-600",
                        )}
                      />
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>

          {shown < total && (
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                onClick={loadMore}
                disabled={loading}
                className="rounded border border-border px-4 py-1.5 font-mono text-xs text-text-dim transition-colors hover:border-accent-cyan hover:text-accent-cyan disabled:opacity-40"
              >
                {loading ? "Loading…" : `Load more (${(total - shown).toLocaleString()} remaining)`}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Hover summary preview */}
      {preview && (
        <div className="pointer-events-none fixed right-6 top-24 z-50 w-80 rounded-lg border border-border bg-surface p-4 shadow-2xl">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-accent-cyan">
            Summary · {preview.id.slice(0, 8)}
          </div>
          <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-text-dim">
            {preview.content}
          </pre>
        </div>
      )}
    </div>
  );
}
