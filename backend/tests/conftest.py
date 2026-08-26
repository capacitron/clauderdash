"""Test-wide isolation.

config.DATA_DIR is bound at import time, so whichever test module imported an
app module first used to decide where every other test wrote. conftest runs
before any test module is imported, which makes this the only safe place to
set it — do not set these vars in individual test files.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["CLAUDE_DASHBOARD_DATA_DIR"] = tempfile.mkdtemp(prefix="claude-dashboard-test-")
os.environ["CLAUDE_DASHBOARD_DEV_MODE"] = "1"
# Keep the vault scan off the real filesystem: the auto-detect walks ~/Documents.
os.environ["CLAUDE_DASHBOARD_OBSIDIAN_DIR"] = tempfile.mkdtemp(prefix="claude-dashboard-vault-")

# config.PREMIUM_TARGETS and CACHE_HIT_TARGET are parsed from CLAUDE.md at
# import. Without this override the suite would read whatever the developer
# happens to have in ~/CLAUDE.md, so a threshold edit in a personal file could
# turn tests red on one machine and green on another. Point at a path that does
# not exist, which exercises the documented fallback.
os.environ["CLAUDE_DRIFT_RULES_FILE"] = str(
    Path(tempfile.mkdtemp(prefix="claude-dashboard-rules-")) / "CLAUDE.md"
)
