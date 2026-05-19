import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet } from "../lib/api";
import { pct } from "../lib/format";
import { Panel } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";

type Action = {
  tier: number;
  color: "red" | "amber" | "green";
  directive: string;
  slash_cmd: string | null;
};

type Resp = {
  weekly_pct: number;
  five_hour_pct: number;
  burn_rate_ratio: number;
  avg_ctx_pct: number;
  opus_share_24h: number;
  eta_seconds: number | null;
  caps_configured: boolean;
  action: Action;
};

const COLOR_CLS: Record<Action["color"], string> = {
  red:   "text-accent-red border-accent-red/40 bg-accent-red/5",
  amber: "text-accent-orange border-accent-orange/40 bg-accent-orange/5",
  green: "text-accent-green border-accent-green/40 bg-accent-green/5",
};

const NEUTRAL_CLS = "text-text border-border";

function fmtEta(secs: number | null): string {
  if (secs === null) return "—";
  if (secs <= 0) return "NOW";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function weeklyClass(pct: number, configured: boolean): string {
  if (!configured) return NEUTRAL_CLS;
  if (pct >= 0.90) return COLOR_CLS.red;
  if (pct >= 0.80) return COLOR_CLS.amber;
  return NEUTRAL_CLS;
}

export function MissionClock() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [etaSecs, setEta] = useState<number | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    const load = () =>
      apiGet<Resp>("/api/mission-clock")
        .then((d) => {
          if (cancel) return;
          setData(d);
          setEta(d.eta_seconds);
          setErr(null);
        })
        .catch((e: unknown) => {
          if (!cancel) setErr(e instanceof Error ? e.message : String(e));
        });
    load();
    const id = setInterval(load, 10_000);
    return () => {
      cancel = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (etaSecs === null) return;
    const id = setInterval(() => {
      setEta((prev) => (prev !== null && prev > 0 ? prev - 1 : prev));
    }, 1000);
    return () => clearInterval(id);
    // Restart tick only when null-ness flips; keeping ETA as dep would reset every second.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [etaSecs === null]);

  const handleDirectiveClick = (slash: string | null) => {
    if (!slash) return;
    if (!navigator.clipboard) {
      window.prompt("Copy this slash command:", slash);
      return;
    }
    navigator.clipboard.writeText(slash).then(() => {
      setCopied(slash);
      setTimeout(() => setCopied(null), 2000);
    }).catch(() => {
      window.prompt("Copy this slash command:", slash);
    });
  };

  const directiveColor: Action["color"] = data?.action.color ?? "green";
  const directiveCls = COLOR_CLS[directiveColor];
  const burnCls = (data?.burn_rate_ratio ?? 0) >= 3.0 ? COLOR_CLS.amber : NEUTRAL_CLS;

  const subtitle = data?.caps_configured
    ? "Env caps active · 10s poll · ETA ticks live"
    : "Set OPUS_* + SONNET_* _CAP_TOKENS env vars to enable runway";

  return (
    <Panel kicker="13 · RUNWAY" title="Runway" subtitle={subtitle}>
      {err ? (
        <div className="text-xs text-accent-red">Error: {err}</div>
      ) : !data ? (
        <Skeleton rows={2} />
      ) : (
        <div className="grid flex-1 grid-cols-2 gap-3 md:grid-cols-4">
          <Tile
            label="Runway ETA"
            value={fmtEta(etaSecs)}
            sub={data.caps_configured ? "time to cap" : "unconfigured"}
            colorCls={data.caps_configured ? directiveCls : NEUTRAL_CLS}
            fill={data.caps_configured ? 1 - Math.min(1, data.weekly_pct) : null}
          />
          <Tile
            label="Burn Rate"
            value={`${data.burn_rate_ratio.toFixed(1)}×`}
            sub="vs 24h avg"
            colorCls={burnCls}
            fill={data.burn_rate_ratio / 5}
          />
          <Tile
            label="Weekly"
            value={data.caps_configured ? pct(data.weekly_pct, 0) : "—"}
            sub={data.caps_configured ? "of cap used" : "unconfigured"}
            colorCls={weeklyClass(data.weekly_pct, data.caps_configured)}
            fill={data.caps_configured ? data.weekly_pct : null}
          />
          <Tile
            label="Directive"
            value={data.action.directive}
            sub={data.action.slash_cmd ?? "no action"}
            colorCls={directiveCls}
            slash={data.action.slash_cmd}
            copied={copied}
            onClick={() => handleDirectiveClick(data.action.slash_cmd)}
          />
        </div>
      )}
    </Panel>
  );
}

function Tile({
  label,
  value,
  sub,
  colorCls,
  fill = null,
  slash = null,
  copied = null,
  onClick,
}: {
  label: string;
  value: string;
  sub: string;
  colorCls: string;
  fill?: number | null;
  slash?: string | null;
  copied?: string | null;
  onClick?: () => void;
}) {
  const clickable = !!slash && !!onClick;
  const isCopied = copied !== null && copied === slash;
  const fillPct = fill !== null ? Math.min(1, Math.max(0, fill)) * 100 : null;

  return (
    <div
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? onClick : undefined}
      onKeyDown={clickable ? (e) => e.key === "Enter" && onClick?.() : undefined}
      className={clsx(
        "flex flex-col rounded-lg border bg-surface-2/40 p-3 transition-colors",
        colorCls,
        clickable && "cursor-pointer hover:bg-surface-3/60 active:scale-[0.98]",
      )}
    >
      <div className="kicker mb-1">{label}</div>
      <div className="truncate font-mono text-xl font-semibold tabular-nums" title={value}>
        {value}
      </div>
      <div className="mt-1 font-mono text-[10px] text-text-subtle">
        {isCopied ? "copied ✓" : sub}
      </div>
      {clickable && !isCopied && (
        <div className="mt-0.5 font-mono text-[10px] text-text-subtle opacity-60">
          click to copy →
        </div>
      )}
      {fillPct !== null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-current/10">
          <div
            className="h-full rounded-full bg-current transition-all duration-700 ease-out"
            style={{ width: `${fillPct}%` }}
          />
        </div>
      )}
    </div>
  );
}
