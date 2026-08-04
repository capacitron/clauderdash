import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  apiGet,
  setActiveProvider,
  type Provider,
  type GaugeData,
} from "./lib/api";
import { fmtCost } from "./lib/format";
import { ArcGauge } from "./components/ArcGauge";
import { SystemHealth } from "./panels/SystemHealth";
import { LiveSessions } from "./panels/LiveSessions";
import { TokenUsage } from "./panels/TokenUsage";
import { ToolLatency } from "./panels/ToolLatency";
import { McpDrillDown } from "./panels/McpDrillDown";
import { SessionOutcomes } from "./panels/SessionOutcomes";
import { CacheEfficiency } from "./panels/CacheEfficiency";
import { Skills } from "./panels/Skills";
import { AgentUsage } from "./panels/AgentUsage";
import { ModelMix } from "./panels/ModelMix";
import { ProjectActivity } from "./panels/ProjectActivity";
import { ContextHeatmap } from "./panels/ContextHeatmap";
import { SessionDrawer } from "./panels/SessionDrawer";
import { Heatmap } from "./panels/Heatmap";
import { MissionClock } from "./panels/MissionClock";
import { TwoStrike } from "./panels/TwoStrike";
import { RevenueLens } from "./panels/RevenueLens";
import { VaultRibbon } from "./panels/VaultRibbon";
import { SuggestionBox } from "./panels/SuggestionBox";
import { Intelligence } from "./panels/Intelligence";
import { Activities } from "./panels/Activities";
import { Yield } from "./panels/Yield";
import { Optimize } from "./panels/Optimize";
import { SessionBrowser } from "./panels/SessionBrowser";
import { PlanUsage } from "./panels/PlanUsage";

type ActiveGauge =
  | "burn"
  | "discipline"
  | "efficiency"
  | "hygiene"
  | "live"
  | null;
type TabId = "overview" | "cost" | "performance" | "hygiene" | "sessions";

const PROVIDERS: { value: Provider; label: string }[] = [
  { value: "all", label: "All" },
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "gemini", label: "Gemini" },
];

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "cost", label: "Cost" },
  { id: "performance", label: "Performance" },
  { id: "hygiene", label: "Hygiene" },
  { id: "sessions", label: "Sessions" },
];

function readTabFromHash(): TabId {
  const fromHash = window.location.hash.slice(1) as TabId;
  return TABS.some((t) => t.id === fromHash) ? fromHash : "overview";
}

export default function App() {
  const [selected, setSelected] = useState<string | null>(null);
  const [provider, setProvider] = useState<Provider>("all");
  const [gauge, setGauge] = useState<ActiveGauge>(null);
  const [gaugeData, setGaugeData] = useState<GaugeData | null>(null);
  // Tab survives reload — a dashboard you leave open on one tab should still be
  // on it after a restart, and it makes a tab linkable via #hash.
  const [tab, setTab] = useState<TabId>(() => readTabFromHash());

  useEffect(() => {
    setActiveProvider(provider);
  }, [provider]);

  // replaceState, not `location.hash = ...`: assigning to hash pushes a history
  // entry per tab click, so Back walks the tabs instead of leaving the page.
  useEffect(() => {
    if (window.location.hash.slice(1) !== tab) {
      window.history.replaceState(null, "", `#${tab}`);
    }
  }, [tab]);

  // Without this, Back/Forward changes the URL while the panels stay put.
  useEffect(() => {
    const onHashChange = () => setTab(readTabFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const load = () =>
      apiGet<GaugeData>("/api/gauge")
        .then(setGaugeData)
        .catch(() => {});
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, []);

  const toggleGauge = (g: ActiveGauge) =>
    setGauge((prev) => (prev === g ? null : g));

  const gd = gaugeData;

  // Fable and Opus each have their own spend-share ceiling now, so the gauge
  // value isn't a raw percentage anymore — the backend already normalises it
  // to "ratio of whichever premium family is closest to its own cap" (1.0 =
  // exactly at cap → 50 on the arc, 1.75x → 87.5). The zone bounds are a
  // fixed convention on that ratio, not a target pulled from the API.
  const disciplineZone = {
    t1: 50,
    t2: 87.5,
    colors: ["green", "amber", "red"] as const,
  };
  const worstFamily = gd?.discipline.worst_family;
  const worstCompliance = worstFamily
    ? gd?.discipline.family_compliance[worstFamily]
    : undefined;

  return (
    <div className="min-h-screen px-6 py-6 xl:px-10 xl:py-8">
      {/* Header */}
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="kicker">CC · SESSION FORENSICS</div>
          <h1 className="mt-1 font-display text-3xl font-bold tracking-tight text-accent-cyan">
            Clauderdash
          </h1>
        </div>
        <div className="flex items-end gap-6">
          {gd && (
            <div className="hidden gap-6 text-right md:flex">
              <HeaderMetric
                label="Today"
                value={fmtCost(gd.burn.today_usd)}
                highlight
              />
              <HeaderMetric label="30d avg" value={fmtCost(gd.burn.avg_usd)} />
              <HeaderMetric
                label="Premium"
                value={`${(gd.discipline.premium_cost_pct * 100).toFixed(0)}%`}
              />
              <HeaderMetric
                label="Cache"
                value={`${(gd.efficiency.hit_rate * 100).toFixed(0)}%`}
              />
            </div>
          )}
          {/* Provider filter lives in the header so it stays visible across
              tabs — it scopes every panel, not just the visible one. */}
          <div className="flex gap-1">
            {PROVIDERS.map((p) => (
              <button
                key={p.value}
                onClick={() => setProvider(p.value)}
                className={clsx(
                  "rounded-full px-3 py-1 font-mono text-xs uppercase tracking-wider transition-colors",
                  provider === p.value
                    ? "bg-accent-cyan/10 border border-accent-cyan/50 text-accent-cyan font-semibold"
                    : "border border-border text-text-dim hover:border-border-glow hover:text-text",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Pinned above the tabs: plan limits + gauges are the "is anything on
          fire" read, and stay visible whichever tab is open. */}
      <PlanUsage />

      {/* Gauge hero row */}
      <div className="mb-1 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <ArcGauge
          kicker="01 · BURN"
          label="Burn"
          value={gd?.burn.value ?? 50}
          statDisplay={fmtCost(gd?.burn.today_usd ?? 0)}
          detail={`30d avg ${fmtCost(gd?.burn.avg_usd ?? 0)}`}
          zone={{ t1: 50, t2: 75, colors: ["green", "amber", "red"] }}
          active={gauge === "burn"}
          onClick={() => toggleGauge("burn")}
        />
        <ArcGauge
          kicker="02 · DISCIPLINE"
          label="Discipline"
          value={gd?.discipline.value ?? 50}
          statDisplay={
            worstCompliance
              ? `${worstCompliance.cost_pct * 100 < 1 ? "<1" : (worstCompliance.cost_pct * 100).toFixed(0)}%`
              : "—"
          }
          detail={
            worstFamily && worstCompliance
              ? `${worstFamily} vs ${(worstCompliance.target_max * 100).toFixed(0)}% cap`
              : "premium spend · 30d"
          }
          zone={disciplineZone}
          active={gauge === "discipline"}
          onClick={() => toggleGauge("discipline")}
        />
        <ArcGauge
          kicker="03 · EFFICIENCY"
          label="Efficiency"
          value={gd?.efficiency.value ?? 50}
          statDisplay={`${((gd?.efficiency.hit_rate ?? 0) * 100).toFixed(0)}%`}
          detail="cache hit · 30d"
          zone={{ t1: 40, t2: 70, colors: ["red", "amber", "green"] }}
          active={gauge === "efficiency"}
          onClick={() => toggleGauge("efficiency")}
        />
        <ArcGauge
          kicker="04 · HYGIENE"
          label="Hygiene"
          value={gd?.hygiene.value ?? 50}
          statDisplay={gd?.hygiene.grade ?? "—"}
          detail={
            gd
              ? `${gd.hygiene.checks_total - gd.hygiene.checks_failing}/${gd.hygiene.checks_total} checks passing`
              : "—"
          }
          zone={{ t1: 55, t2: 75, colors: ["red", "amber", "green"] }}
          active={gauge === "hygiene"}
          onClick={() => toggleGauge("hygiene")}
        />
        <ArcGauge
          variant="live"
          kicker="05 · LIVE"
          label="Live"
          count={gd?.live.count ?? 0}
          active={gauge === "live"}
          onClick={() => toggleGauge("live")}
        />
      </div>

      {/* Expanded gauge panel */}
      {gauge && (
        <div key={gauge} className="mb-6 mt-3 animate-fade-in">
          {gauge === "burn" && <RevenueLens />}
          {gauge === "discipline" && <ModelMix />}
          {gauge === "efficiency" && <CacheEfficiency />}
          {gauge === "hygiene" && <Optimize />}
          {gauge === "live" && <LiveSessions onSelect={setSelected} />}
        </div>
      )}

      {/* Tab bar */}
      <div
        role="tablist"
        aria-label="Dashboard sections"
        className="mb-6 flex gap-1 border-b border-border"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              "-mb-px border-b-2 px-4 py-2 font-mono text-xs uppercase tracking-wider transition-colors",
              tab === t.id
                ? "border-accent-cyan text-accent-cyan font-semibold"
                : "border-transparent text-text-dim hover:text-text",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Panels. Each tab mounts only its own panels, so switching tabs stops
          the hidden panels' polling instead of leaving 19 fetch loops running. */}
      <main>
        {tab === "overview" && (
          <div className="columns-1 gap-5 md:columns-2 xl:columns-3">
            <Intelligence />
            <LiveSessions onSelect={setSelected} />
            <ProjectActivity />
            <Activities />
            <Heatmap />
            <VaultRibbon />
          </div>
        )}

        {tab === "cost" && (
          <div className="columns-1 gap-5 md:columns-2 xl:columns-3">
            <TokenUsage />
            <ModelMix />
            <RevenueLens />
            <CacheEfficiency />
            <Yield />
          </div>
        )}

        {tab === "performance" && (
          <>
            <SystemHealth />
            <MissionClock />
            <div className="columns-1 gap-5 md:columns-2 xl:columns-3">
              <ToolLatency />
              <McpDrillDown />
              <ContextHeatmap onSelect={setSelected} />
              <Skills />
              <AgentUsage />
            </div>
          </>
        )}

        {tab === "hygiene" && (
          <div className="columns-1 gap-5 md:columns-2 xl:columns-3">
            <SuggestionBox />
            <Optimize />
            <TwoStrike />
            <SessionOutcomes />
          </div>
        )}

        {tab === "sessions" && <SessionBrowser onSelect={setSelected} />}
      </main>

      <footer className="mt-10 border-t border-border pt-6 text-center font-mono text-[10px] uppercase tracking-[0.2em] text-text-subtle">
        Arch · Python + SQLite · localhost-only · no accounts, no tracking
      </footer>

      <SessionDrawer id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function HeaderMetric({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <div className="kicker">{label}</div>
      <div
        className={clsx(
          "font-mono text-sm font-semibold tabular-nums",
          highlight ? "text-accent-cyan" : "text-text",
        )}
      >
        {value}
      </div>
    </div>
  );
}
