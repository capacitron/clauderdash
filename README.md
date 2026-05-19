# Claude Code Dashboard

Local observability for Claude Code. Arch Linux native. Forensic diary, not mission control.

Ten panels over the data Claude Code already writes to your disk:

1. **Live sessions** — what's running right now
2. **Token usage** — by day, stacked (input/output/cache-read/cache-create)
3. **Tool latency** — p50/p95/max/error-rate per tool
4. **MCP drill-down** — per-server and per-tool performance (centerpiece)
5. **Session outcomes** — errored / rate_limited / truncated / unfinished / ok
6. **Cache efficiency** — hit rate, target ≥ 70%
7. **Skills** — slash-command activations + tokens + tokens per run
8. **Model mix** — Opus / Sonnet / Haiku % split, Opus target ≤ 20%
9. **Project activity** — grouped by CWD
10. **Context heatmap** — live sessions, % of window used, compact at 40%

Runs entirely on your machine. `127.0.0.1:7777` — no cloud, no account, no outbound telemetry.

## Install

```bash
cd ~/projects/claude-dashboard
./install.sh
```

What it does:

1. Creates Python venv, installs FastAPI + uvicorn + watchdog
2. Builds the React/Vite/Tailwind frontend
3. Adds `cc.local → 127.0.0.1` to `/etc/hosts` (requires sudo)
4. Installs a systemd **user** service and enables it
5. Waits until the server responds on 127.0.0.1:7777

## Enable OTEL telemetry (recommended)

Without this, panels still work from the JSONL logs — but OTEL gives you real-time streaming
plus event types (compaction, hook execution, API errors) that are not in JSONL.

```bash
./scripts/setup-otel.sh
# Then QUIT and restart Claude Code.
```

The setup helper backs up `~/.claude/settings.json` before writing and only adds keys that are
not already present — it never overwrites your own config.

## Verify

```bash
./scripts/doctor.sh
```

Exit 0 + all green rows = everything is working.

## URLs

| Surface | URL |
| --- | --- |
| Dashboard | http://cc.local:7777 |
| Alt domain | http://claude.local:7777 |
| Alt domain | http://dashboard.local:7777 |
| Plain | http://localhost:7777 |
| API docs | http://cc.local:7777/api/docs |
| Health | http://cc.local:7777/healthz |

## Service control

```bash
systemctl --user status claude-dashboard
systemctl --user restart claude-dashboard
systemctl --user stop claude-dashboard
journalctl --user -u claude-dashboard -f
```

## Dev mode (hot reload both ends)

```bash
./scripts/dev.sh
# Frontend: http://localhost:5173 (Vite HMR, proxies /api → :7777)
# Backend:  http://localhost:7777 (uvicorn --reload)
```

Stops the systemd service while running. Restart it manually when done:

```bash
systemctl --user start claude-dashboard
```

## Fixture mode

Seed 50 synthetic sessions + ~1000 tool calls so panels render instantly on a fresh install:

```bash
CLAUDE_DASHBOARD_DEV_MODE=1 ./install.sh
```

## Suggestion Box (opt-in, outbound network)

A weekly forensic synthesis panel. Runs `claude -p --model haiku` against six aggregate
SQL queries and writes up to 5 recommendations to `data/recommendations.json`. The panel
at the bottom of the dashboard renders them with evidence SQL attached to each rec.

**This is the one exception to the "no outbound network" rule.** It is opt-in.

### Privacy

- Six allowlisted aggregate queries. No ad-hoc SQL path.
- `input_preview` field is **structurally excluded** from all queries. Live DB inspection
  confirmed 22 `sk-ant-api03-*` API key fragments in that field — never sent to Anthropic.
- `error_preview` truncated to 80 chars in every query (zero API-key hits confirmed there).
- No session IDs, no raw prompts/responses in outgoing payload.
- Hard cost cap via `--max-budget-usd 0.15`. Typical run: ~$0.05–0.10.
- Every run logged to `data/generator.log` (one JSON line per run with tokens + cost).

### Manual run (try before enabling timer)

```bash
cd ~/projects/claude-dashboard
./backend/.venv/bin/python scripts/generate_recommendations.py --dry-run   # prints SQL results only
./backend/.venv/bin/python scripts/generate_recommendations.py             # real run (calls Haiku)
cat data/recommendations.json | python3 -m json.tool
tail -1 data/generator.log
```

Then refresh the dashboard. The SUGGESTIONS panel populates.

### Enable weekly timer (Sunday 09:00)

```bash
PROJECT_DIR="$HOME/projects/claude-dashboard"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/systemd/claude-dashboard-suggestions.service" \
    > "$HOME/.config/systemd/user/claude-dashboard-suggestions.service"
cp "$PROJECT_DIR/systemd/claude-dashboard-suggestions.timer" \
   "$HOME/.config/systemd/user/claude-dashboard-suggestions.timer"
systemctl --user daemon-reload
systemctl --user enable --now claude-dashboard-suggestions.timer
systemctl --user list-timers | grep suggestions
```

To disable:

```bash
systemctl --user disable --now claude-dashboard-suggestions.timer
```

### Banned-phrase filter

The generator ships a WARN-ONLY regex over recommendation `title + body`. Logs
`filter_would_reject_count=N` to stderr (captured in `journalctl --user -u
claude-dashboard-suggestions` when the timer runs). Pass_1 does not remove
flagged recs — it only counts them. After 2–3 weeks of data you decide whether
to promote the filter to rejection or delete it. Regex is in
`scripts/generate_recommendations.py` (`BANNED_PHRASE_RE`).

## What this is not

- Not a "mission control" for agents. No task queue, no HITL, no Telegram.
- Not cloud-sync. One SQLite file at `data/dashboard.db`. Delete it to reset.
- Not a cost estimator in dollars. Pricing varies by plan — I don't want the dashboard guessing.

## Architecture

```
Claude Code ─┬─ writes JSONL ─→ ~/.claude/projects/
             │
             └─ emits OTEL    ─→ POST http://localhost:7777/v1/logs
                                      /v1/metrics

Dashboard (FastAPI :7777)
   ├─ watcher loop: polls projects dir every 2s, parses new lines
   ├─ OTEL receiver: /v1/logs + /v1/metrics, stores firehose
   ├─ SQLite (WAL mode): sessions / messages / tool_calls / skills / otel_events
   └─ SPA: React + Vite + Tailwind + Recharts, served from /frontend/dist
```

## Layout

```
backend/
  app/
    main.py              FastAPI entry, SPA mount, startup hooks
    config.py            Paths, port, dev-mode flag, model windows
    db.py                SQLite WAL connection helpers
    schema.sql           Tables + indexes
    ingest/
      jsonl.py           JSONL parser + incremental sync
      watcher.py         Background poll loop
      otel.py            OTLP/HTTP JSON receiver
      fixtures.py        DEV_MODE seeding
    api/
      panels.py          All 10 panel endpoints + summary + doctor
      ranges.py          Range helpers + percentile
frontend/
  src/
    App.tsx              Dashboard grid + drawer
    components/          Panel, RangeToggle, RelativeTime, Skeleton
    panels/              One file per panel (10) + SessionDrawer
    lib/                 api + format utilities
scripts/
  dev.sh                 Dev server launcher
  doctor.sh              Deterministic diagnostic
  setup-otel.sh          Merge OTEL env keys into settings.json
systemd/
  claude-dashboard.service   User unit template (path patched at install)
data/
  dashboard.db           SQLite, created on first run
install.sh               Everything above, idempotent
```
