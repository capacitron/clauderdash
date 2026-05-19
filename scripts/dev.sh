#!/usr/bin/env bash
# Dev server with auto-reload + Vite HMR.
# Backend: uvicorn --reload on :7777
# Frontend: vite dev on :5173 (proxies /api → :7777)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

if [[ ! -d backend/.venv ]]; then
  echo "Run ./install.sh first."
  exit 1
fi

# Stop system service while dev server is up
if systemctl --user is-active --quiet claude-dashboard 2>/dev/null; then
  echo "Stopping systemd service..."
  systemctl --user stop claude-dashboard
fi

# Backend in background
source backend/.venv/bin/activate
(cd backend && uvicorn app.main:app --host 127.0.0.1 --port 7777 --reload) &
BACK_PID=$!
trap "kill $BACK_PID 2>/dev/null || true" EXIT

# Frontend
(cd frontend && npm run dev)
