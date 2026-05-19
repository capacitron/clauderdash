#!/usr/bin/env bash
# Claude Code Dashboard installer — Arch Linux.
# Idempotent. Run multiple times safely.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

GREEN=$'\e[32m'
YELLOW=$'\e[33m'
BLUE=$'\e[34m'
RED=$'\e[31m'
DIM=$'\e[2m'
NC=$'\e[0m'

say()  { printf "%s\n" "${BLUE}▶${NC} $*"; }
ok()   { printf "%s\n" "${GREEN}✓${NC} $*"; }
warn() { printf "%s\n" "${YELLOW}!${NC} $*"; }
err()  { printf "%s\n" "${RED}✗${NC} $*" >&2; }

# ---- 1. Arch + Python sanity ----------------------------------------------
if ! command -v python3 >/dev/null; then
  err "python3 not found. Install: sudo pacman -S python"
  exit 1
fi
PY_MAJOR=$(python3 -c 'import sys;print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys;print(sys.version_info.minor)')
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ) ]]; then
  err "Python ≥ 3.11 required. Found: $PY_MAJOR.$PY_MINOR"
  exit 1
fi
ok "Python $PY_MAJOR.$PY_MINOR"

if ! command -v node >/dev/null; then
  err "node not found. Install: sudo pacman -S nodejs npm"
  exit 1
fi
ok "Node $(node -v)"

# ---- 2. Backend venv -------------------------------------------------------
say "Creating backend venv..."
python3 -m venv backend/.venv
# shellcheck source=/dev/null
source backend/.venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e backend
ok "Backend deps installed"

# ---- 3. Frontend build -----------------------------------------------------
say "Installing frontend deps..."
(cd frontend && npm install --silent)
say "Building frontend..."
(cd frontend && npm run build --silent)
ok "Frontend bundled at frontend/dist/"

# ---- 4. /etc/hosts entry ---------------------------------------------------
HOSTS_LINE="127.0.0.1    cc.local claude.local dashboard.local"
if ! grep -qF "cc.local" /etc/hosts 2>/dev/null; then
  warn "Adding cc.local → 127.0.0.1 to /etc/hosts (needs sudo)"
  echo "$HOSTS_LINE" | sudo tee -a /etc/hosts >/dev/null
  ok "/etc/hosts updated"
else
  ok "/etc/hosts already has cc.local"
fi

# ---- 5. systemd user unit --------------------------------------------------
UNIT_SRC="systemd/claude-dashboard.service"
UNIT_DST="$HOME/.config/systemd/user/claude-dashboard.service"
mkdir -p "$(dirname "$UNIT_DST")"
sed "s|__PROJECT_DIR__|$HERE|g" "$UNIT_SRC" > "$UNIT_DST"
systemctl --user daemon-reload
systemctl --user enable --now claude-dashboard.service
ok "systemd user service enabled"

# ---- 6. Health check -------------------------------------------------------
say "Waiting for server..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fs http://127.0.0.1:7777/healthz >/dev/null 2>&1; then
    ok "Server up: http://127.0.0.1:7777/healthz"
    break
  fi
  sleep 1
done
if ! curl -fs http://127.0.0.1:7777/healthz >/dev/null 2>&1; then
  err "Server failed to start. Check: journalctl --user -u claude-dashboard -n 50"
  exit 1
fi

# ---- 7. Done ---------------------------------------------------------------
cat <<EOF

${GREEN}INSTALLED.${NC}

Dashboard:       http://cc.local:7777
                 http://claude.local:7777
                 http://localhost:7777
API docs:        http://cc.local:7777/api/docs
Doctor:          ./scripts/doctor.sh

${DIM}Enable OTEL telemetry so Claude Code posts events in real time:${NC}
  ./scripts/setup-otel.sh
  # Then QUIT and restart Claude Code.

${DIM}Service control:${NC}
  systemctl --user status claude-dashboard
  systemctl --user restart claude-dashboard
  journalctl --user -u claude-dashboard -f

EOF
