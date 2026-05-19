#!/usr/bin/env bash
# Merge OTEL env keys into ~/.claude/settings.json with a timestamped backup.
# Never overwrites keys you already set.
set -euo pipefail

SETTINGS="$HOME/.claude/settings.json"
BACKUP="$HOME/.claude/settings.json.bak.$(date +%Y%m%d-%H%M%S)"

if [[ ! -f "$SETTINGS" ]]; then
  echo "settings.json not found at $SETTINGS — creating it."
  mkdir -p "$(dirname "$SETTINGS")"
  echo '{}' > "$SETTINGS"
fi

cp "$SETTINGS" "$BACKUP"
echo "Backup: $BACKUP"

python3 - "$SETTINGS" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
env = data.setdefault("env", {})
desired = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:7777",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_LOG_TOOL_DETAILS": "1",
}
added = []
kept = []
for k, v in desired.items():
    if k in env:
        kept.append(f"{k}={env[k]} (unchanged)")
    else:
        env[k] = v
        added.append(f"{k}={v}")
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("\nAdded:")
for line in added:
    print(f"  + {line}")
if kept:
    print("\nAlready present (not overwritten):")
    for line in kept:
        print(f"  · {line}")
PY

cat <<EOF

NEXT: quit Claude Code completely and restart it.
      Env vars only apply to new sessions.

Verify with:
  curl -s http://localhost:7777/api/summary | python3 -m json.tool | grep otel_events
EOF
