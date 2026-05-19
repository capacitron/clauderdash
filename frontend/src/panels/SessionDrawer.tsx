import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet } from "../lib/api";
import { ms, shortNum, projectLabel, relativeTime } from "../lib/format";

type ToolCall = {
  tool_use_id: string;
  tool_name: string;
  is_mcp: number;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  is_error: number | null;
  input_preview: string | null;
  error_preview: string | null;
};

type Detail = {
  session: { id: string; cwd: string; model: string; started_at: string; outcome: string };
  usage: { i: number; o: number; cr: number; cc: number; n_messages: number };
  tools: ToolCall[];
};

export function SessionDrawer({
  id,
  onClose,
}: {
  id: string | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);

  useEffect(() => {
    if (!id) {
      setDetail(null);
      return;
    }
    apiGet<Detail>(`/api/session/${id}`).then(setDetail).catch(() => setDetail(null));
  }, [id]);

  if (!id) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-bg/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <aside
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-xl animate-fade-in flex-col overflow-hidden border-l border-border bg-surface shadow-2xl"
      >
        <header className="flex items-start justify-between border-b border-border p-5">
          <div>
            <div className="kicker mb-1">Session detail</div>
            <h3 className="font-mono text-sm text-text">{id}</h3>
            {detail && (
              <p className="mt-1 text-xs text-text-dim">
                {projectLabel(detail.session.cwd)} · {detail.session.model?.replace(/^claude-/, "")}
                {" · "}started {relativeTime(detail.session.started_at)}
              </p>
            )}
          </div>
          <button onClick={onClose} className="btn">
            Close
          </button>
        </header>
        {!detail ? (
          <div className="p-6 text-sm text-text-dim">Loading…</div>
        ) : (
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5 scrollbar-thin">
            <section className="grid grid-cols-4 gap-3">
              <Stat label="Input" value={shortNum(detail.usage.i)} />
              <Stat label="Output" value={shortNum(detail.usage.o)} />
              <Stat label="Cache read" value={shortNum(detail.usage.cr)} />
              <Stat label="Cache create" value={shortNum(detail.usage.cc)} />
            </section>
            <section>
              <div className="kicker mb-2">Tool timeline · {detail.tools.length} calls</div>
              <ul className="flex flex-col gap-1">
                {detail.tools.map((t) => (
                  <li
                    key={t.tool_use_id}
                    className={clsx(
                      "rounded-md border border-border bg-surface-2/40 p-2 text-xs font-mono",
                      t.is_error && "border-accent-red/60",
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className={clsx(t.is_mcp && "text-accent-purple")}>{t.tool_name}</span>
                      <span className="text-text-subtle">{ms(t.duration_ms ?? null)}</span>
                    </div>
                    {t.input_preview && (
                      <div className="mt-1 truncate text-[11px] text-text-subtle">
                        {t.input_preview}
                      </div>
                    )}
                    {t.is_error && t.error_preview && (
                      <div className="mt-1 text-[11px] text-accent-red">{t.error_preview}</div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-2/40 p-3">
      <div className="kicker">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-text tabular-nums">{value}</div>
    </div>
  );
}
