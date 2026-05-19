"""Centralised configuration. Environment overrides everything."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

# Source of truth for Claude Code session data
CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", HOME / ".claude" / "projects")
)

# Where we persist the SQLite database
DATA_DIR = Path(
    os.environ.get(
        "CLAUDE_DASHBOARD_DATA_DIR",
        Path(__file__).resolve().parent.parent.parent / "data",
    )
)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "dashboard.db"

# Path to the compiled frontend (populated by install.sh via `npm run build`)
FRONTEND_DIST = Path(
    os.environ.get(
        "CLAUDE_DASHBOARD_FRONTEND",
        Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
    )
)

# Bind host + port
HOST = os.environ.get("CLAUDE_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAUDE_DASHBOARD_PORT", "7777"))

# DEV_MODE seeds fixtures so panels render with zero real data
DEV_MODE = os.environ.get("CLAUDE_DASHBOARD_DEV_MODE", "0") == "1"

# Tool latency cap (defensive — orphan pairs from crashed sessions)
MAX_TOOL_DURATION_MS = 10 * 60 * 1000  # 10 minutes

# Liveness window for "currently running" sessions
LIVE_WINDOW_SECONDS = 5 * 60

# Context window sizes by model (for context-heatmap panel)
MODEL_CONTEXT_WINDOWS = {
    "claude-opus-4": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4-6": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-sonnet-4": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4": 200_000,
    "claude-haiku-4-5": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000

# Model family mapping for mix panel
MODEL_FAMILIES = {
    "opus": ["claude-opus"],
    "sonnet": ["claude-sonnet"],
    "haiku": ["claude-haiku"],
}

# v2 panels ------------------------------------------------------------------

# Heatmap window (weeks)
HEATMAP_WEEKS = 12

# Obsidian vault for vault-ribbon panel
OBSIDIAN_DIR = Path(
    os.environ.get("CLAUDE_DASHBOARD_OBSIDIAN_DIR", HOME / "Documents" / "ObsidianVault")
)
# rglob order is filesystem-defined; to get the real top-10 by mtime we must
# stat every candidate. Default cap covers typical vaults; env-override for
# pathological sizes. Obsidian scan is ~15ms per 2000 files on SSD.
OBSIDIAN_MAX_SCAN_FILES = int(
    os.environ.get("CLAUDE_DASHBOARD_OBSIDIAN_MAX_FILES", "5000")
)

# cwd prefix → tag mapping for revenue-lens. First-match wins. Empty list
# means everything lands in the untagged bucket (graceful degradation).
# Populate with your own project paths, e.g.:
#   (str(HOME / "projects" / "myapp"), "myapp"),
PROJECT_TAG_RULES: list[tuple[str, str]] = []

# v3 suggestion box (pass_1) -------------------------------------------------
SUGGESTIONS_FILE = DATA_DIR / "recommendations.json"
SUGGESTIONS_LOG_FILE = DATA_DIR / "generator.log"
