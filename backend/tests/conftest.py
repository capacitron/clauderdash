"""Test-wide isolation.

config.DATA_DIR is bound at import time, so whichever test module imported an
app module first used to decide where every other test wrote. conftest runs
before any test module is imported, which makes this the only safe place to
set it — do not set these vars in individual test files.
"""
from __future__ import annotations

import os
import tempfile

os.environ["CLAUDE_DASHBOARD_DATA_DIR"] = tempfile.mkdtemp(prefix="claude-dashboard-test-")
os.environ["CLAUDE_DASHBOARD_DEV_MODE"] = "1"
# Keep the vault scan off the real filesystem: the auto-detect walks ~/Documents.
os.environ["CLAUDE_DASHBOARD_OBSIDIAN_DIR"] = tempfile.mkdtemp(prefix="claude-dashboard-vault-")
