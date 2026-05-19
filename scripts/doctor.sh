#!/usr/bin/env bash
# Deterministic health check. No LLM calls. Exits 0 iff all green.
set -euo pipefail

GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; DIM=$'\e[2m'; NC=$'\e[0m'

pass() { printf "%s\n" "${GREEN}✓${NC} $*"; }
fail() { printf "%s\n" "${RED}✗${NC} $*" >&2; FAILED=1; }
warn() { printf "%s\n" "${YELLOW}!${NC} $*"; }

FAILED=0

# Local sanity
if command -v python3 >/dev/null; then
  pass "python3 $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
else fail "python3 missing"; fi

if [[ -d "$HOME/.claude/projects" ]]; then
  COUNT=$(find "$HOME/.claude/projects" -name '*.jsonl' 2>/dev/null | wc -l)
  pass "~/.claude/projects (${COUNT} sessions)"
else fail "~/.claude/projects not found — run Claude Code at least once"; fi

if systemctl --user is-active --quiet claude-dashboard 2>/dev/null; then
  pass "systemd service active"
else warn "systemd service not active (run install.sh or: systemctl --user start claude-dashboard)"; fi

# Server reachability
if ! curl -fs --max-time 3 http://127.0.0.1:7777/healthz >/dev/null; then
  fail "server not reachable on 127.0.0.1:7777"
  exit 1
fi
pass "server reachable (http://127.0.0.1:7777)"

# Ask the server for its own view
RAW=$(curl -fs http://127.0.0.1:7777/api/doctor)
python3 <<PY
import json, sys
r = json.loads('''$RAW''')
for c in r["checks"]:
    mark = "✓" if c["ok"] else "✗"
    col = "\033[32m" if c["ok"] else "\033[31m"
    print(f"{col}{mark}\033[0m {c['name']}: {c['detail']}")
sys.exit(0 if r["all_green"] else 1)
PY
DSTATUS=$?

if [[ $FAILED -ne 0 || $DSTATUS -ne 0 ]]; then
  exit 1
fi
printf "\n${GREEN}ALL GREEN.${NC}\n"
