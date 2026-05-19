import { useEffect, useState } from "react";
import { relativeTime } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";
import obsidianLogo from "../assets/obsidian-logo.svg";

type File = { name: string; mtime_iso: string };
type Resp = {
  files: File[];
  scanned: number;
  capped: boolean;
  vault_exists: boolean;
};

export function VaultRibbon() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    fetch("/api/vault-ribbon", { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return (await r.json()) as Resp;
      })
      .then((d) => {
        clearTimeout(timeout);
        setData(d);
      })
      .catch((e) => {
        clearTimeout(timeout);
        setErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  const subtitle = data
    ? data.capped
      ? `top 10 of ${data.scanned}+ scanned (capped)`
      : `${data.scanned} files scanned`
    : "Obsidian vault · last 10 touched";

  return (
    <Panel
      kicker="15 · VAULT"
      title="OBSIDIAN RIBBON"
      titleIcon={<img src={obsidianLogo} alt="Obsidian" className="h-5 w-5" />}
      subtitle={subtitle}
    >
      {err ? (
        <EmptyState label={`Vault scan failed: ${err}`} />
      ) : !data ? (
        <Skeleton rows={4} />
      ) : !data.vault_exists ? (
        <EmptyState label="Obsidian vault not found. Set CLAUDE_DASHBOARD_OBSIDIAN_DIR." />
      ) : data.files.length === 0 ? (
        <EmptyState label="No .md files in Obsidian vault." />
      ) : (
        <ul className="flex flex-1 flex-col gap-1 overflow-y-auto scrollbar-thin">
          {data.files.map((f, i) => (
            <li
              key={`${f.name}-${i}`}
              className="flex items-center justify-between gap-3 rounded-md border border-border bg-surface-2/40 px-3 py-2"
            >
              <span className="truncate text-xs text-text">{f.name}</span>
              <span className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
                {relativeTime(f.mtime_iso)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
