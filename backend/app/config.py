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

# Context windows live in ingest.pricing (LiteLLM-sourced, refreshed daily).
# A second hardcoded table here went stale and made the context-heatmap divide
# 1M-window models by 200k.

# Model family mapping for mix panel. Order matters — first prefix match wins.
MODEL_FAMILIES = {
    "fable": ["claude-fable"],
    "opus": ["claude-opus"],
    "sonnet": ["claude-sonnet"],
    "haiku": ["claude-haiku"],
}
# Derived, never hand-maintained: a family present here but missing from the
# order list would KeyError the bucket dicts in /api/models.
MODEL_FAMILY_ORDER = [*MODEL_FAMILIES, "unknown"]

# Premium families each get their OWN spend-share ceiling, not one shared cap.
# A single combined target stops making sense once the premium tier has two
# models at different price multiples over Sonnet: Fable 5 ($10/$50) is still
# ~5x Sonnet 5's price, the ratio Opus used to sit at, while Opus 5 ($5/$25)
# dropped to ~2.5x after its own price cut. Lumping them under one 20% cap
# treated both as equally worth avoiding, which stopped being true the moment
# Opus got cheaper — a cap tuned for a 5x multiplier is too strict for a 2.5x
# one. Tracks share of *spend*, not token count, so a price change alone
# doesn't force a threshold retune the way a token-share target would.
PREMIUM_TARGETS: dict[str, float] = {
    "fable": 0.10,
    "opus": 0.30,
}
PREMIUM_FAMILIES = tuple(PREMIUM_TARGETS)

# v2 panels ------------------------------------------------------------------

# Heatmap window (weeks)
HEATMAP_WEEKS = 12

# Obsidian vault for vault-ribbon panel. Any vault has a `.obsidian/` dir at
# its root, so find it by marker instead of guessing a folder name.
def _find_obsidian_vault() -> Path:
    override = os.environ.get("CLAUDE_DASHBOARD_OBSIDIAN_DIR")
    if override:
        return Path(override).expanduser()
    for parent in (HOME / "Documents", HOME):
        try:
            candidates = [d for d in parent.iterdir() if (d / ".obsidian").is_dir()]
        except OSError:
            continue
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            # Tie-break on note count; only walk the tree when there's a choice.
            return max(candidates, key=lambda d: sum(1 for _ in d.rglob("*.md")))
    return HOME / "Documents" / "ObsidianVault"


OBSIDIAN_DIR = _find_obsidian_vault()
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
