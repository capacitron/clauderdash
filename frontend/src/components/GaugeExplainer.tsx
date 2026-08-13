type GaugeId = "burn" | "discipline" | "efficiency" | "hygiene" | "live";

const COPY: Record<GaugeId, { technical: string; plain: string }> = {
  burn: {
    technical:
      "Today's total spend (sum of cost_usd across all messages, UTC day) divided by the trailing 30-day daily average, scaled so ratio 1.0 (today == average) lands the needle at 50/100. Capped at 100.",
    plain:
      "How much you've spent today next to a normal day. Needle in the middle = spending like usual. Needle in the red = today is running way above your typical pace — check what's eating spend before it compounds over the month.",
  },
  discipline: {
    technical:
      "Each premium model family (Opus, Fable) has its own 30-day spend-share cap. This gauge tracks whichever family is furthest over its own cap: ratio = family's cost share ÷ its target, needle = min(100, worst_ratio × 50) — so exactly at cap sits at 50, 1.75x over sits at 87.5.",
    plain:
      "Are the expensive models eating more of the budget than they should? Each pricey model (Opus, Fable) has its own limit. If the needle's in the red, one of them blew past its own cap — worth checking which one and whether that work actually needed it.",
  },
  efficiency: {
    technical:
      "Cache hit rate over 30 days: cache_read_tokens ÷ (input_tokens + cache_read_tokens + cache_create_tokens). Higher is better — cached reads are billed at a steep discount vs fresh input.",
    plain:
      "How much of your token usage is getting the cheap cache-read discount instead of paying full price to re-send context. Higher % = more savings. Low % means sessions keep starting cold or context isn't being reused.",
  },
  hygiene: {
    technical:
      "Score derived from open detector findings (unresolved, not accepted/snoozed) across a fixed checklist — stale skills, unused MCP connections, drift against the CLAUDE.md targets table, repeat tool-error fingerprints, etc. Letter grade + failing/total checks shown.",
    plain:
      "A housekeeping score for the assistant setup itself — leftover config, unused integrations, repeated errors nobody fixed. Higher grade = fewer nagging issues sitting around. Click through to see exactly what's flagged and fix it from there.",
  },
  live: {
    technical:
      "Count of sessions with last_modified_ts inside the live window (recently active), needle scaled against a practical ceiling of 5 concurrent sessions — Anthropic sets no hard cap on instances, but pooled rate limits and human review both bottleneck well before that. Detail line is the last_user_message of whichever session was modified most recently.",
    plain:
      "How many Claude Code windows are actively working right now, and what the most recently active one is doing. There's no real limit on how many you can open — the needle just flags when you're running enough in parallel that your rate limits (or your own attention) start to strain.",
  },
};

export function GaugeExplainer({ gauge }: { gauge: GaugeId }) {
  const c = COPY[gauge];
  return (
    <div className="mb-3 rounded-xl border border-border bg-surface px-4 py-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <div className="kicker mb-1 text-text-subtle">Technical</div>
          <p className="text-xs leading-relaxed text-text-dim">{c.technical}</p>
        </div>
        <div>
          <div className="kicker mb-1 text-accent-cyan">In plain terms</div>
          <p className="text-xs leading-relaxed text-text-dim">{c.plain}</p>
        </div>
      </div>
    </div>
  );
}
