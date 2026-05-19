import { useEffect, useState } from "react";
import clsx from "clsx";
import { apiGet, setActiveProvider, type Provider, type GaugeData } from "./lib/api";
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

type ActiveGauge = "burn" | "discipline" | "efficiency" | "hygiene" | "live" | null;
type ActiveView = "dashboard" | "sessions";

const PROVIDERS: { value: Provider; label: string }[] = [
  { value: "all",          label: "All"        },
  { value: "claude-code",  label: "Claude Code" },
  { value: "codex",        label: "Codex"      },
  { value: "gemini",       label: "Gemini"     },
];

export default function App() {
  const [selected,  setSelected]  = useState<string | null>(null);
  const [provider,  setProvider]  = useState<Provider>("all");
  const [gauge,     setGauge]     = useState<ActiveGauge>(null);
  const [gaugeData, setGaugeData] = useState<GaugeData | null>(null);
  const [view,      setView]      = useState<ActiveView>("dashboard");

  useEffect(() => { setActiveProvider(provider); }, [provider]);

  useEffect(() => {
    const load = () =>
      apiGet<GaugeData>("/api/gauge").then(setGaugeData).catch(() => {});
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, []);

  const toggleGauge = (g: ActiveGauge) =>
    setGauge((prev) => (prev === g ? null : g));

  const gd = gaugeData;

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
              <HeaderMetric label="Today" value={fmtCost(gd.burn.today_usd)} highlight />
              <HeaderMetric label="30d avg" value={fmtCost(gd.burn.avg_usd)} />
              <HeaderMetric label="Opus" value={`${(gd.discipline.opus_pct * 100).toFixed(0)}%`} />
              <HeaderMetric label="Cache" value={`${(gd.efficiency.hit_rate * 100).toFixed(0)}%`} />
            </div>
          )}
          <div className="flex gap-1">
            {(["dashboard", "sessions"] as ActiveView[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={clsx(
                  "rounded-full px-3 py-1 font-mono text-xs uppercase tracking-wider transition-colors",
                  view === v
                    ? "bg-accent-cyan/10 border border-accent-cyan/50 text-accent-cyan font-semibold"
                    : "border border-border text-text-dim hover:border-border-glow hover:text-text",
                )}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Sessions view */}
      {view === "sessions" && (
        <SessionBrowser onSelect={setSelected} />
      )}

      {/* Dashboard view */}
      {view === "dashboard" && <>

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
          statDisplay={`${(gd?.discipline.opus_pct ?? 0) * 100 < 1 ? "<1" : ((gd?.discipline.opus_pct ?? 0) * 100).toFixed(0)}%`}
          detail="opus · 30d"
          zone={{ t1: 20, t2: 35, colors: ["green", "amber", "red"] }}
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
          detail={`${gd?.hygiene.score ?? "—"} / 100`}
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
          {gauge === "burn"        && <RevenueLens />}
          {gauge === "discipline"  && <ModelMix />}
          {gauge === "efficiency"  && <CacheEfficiency />}
          {gauge === "hygiene"     && <Optimize />}
          {gauge === "live"        && <LiveSessions onSelect={setSelected} />}
        </div>
      )}

      {/* Provider filter */}
      <div className="mb-6 flex gap-2">
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

      {/* Panel grid */}
      <main>
        <SystemHealth />
        <MissionClock />
        <div className="columns-1 gap-5 md:columns-2 xl:columns-3">
          <Intelligence />
          <LiveSessions onSelect={setSelected} />
          <TokenUsage />
          <ToolLatency />
          <McpDrillDown />
          <SessionOutcomes />
          <CacheEfficiency />
          <Skills />
          <ModelMix />
          <ProjectActivity />
          <Activities />
          <Yield />
          <Optimize />
          <ContextHeatmap onSelect={setSelected} />
          <Heatmap />
          <TwoStrike />
          <RevenueLens />
          <VaultRibbon />
          <SuggestionBox />
        </div>
      </main>

      </> /* end dashboard view */}

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
