"""Monitor hook status (enabled/disabled) from ~/.claude/hooks/."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from ..db import connect as get_db

log = logging.getLogger("hook-monitor")

HOOKS_DIR = Path.home() / ".claude" / "hooks"


def scan_hook_directory() -> dict[str, dict]:
    """
    Scan hooks directory for .disabled files and execution state.
    Returns {hook_name: {status, last_ran_at, ...}}
    """
    hooks_status = {}

    if not HOOKS_DIR.exists():
        return hooks_status

    # List all shell scripts (ending in .sh, excluding .disabled)
    for item in HOOKS_DIR.iterdir():
        if not item.is_file():
            continue

        name = item.name

        # Skip disabled files in first pass
        if name.endswith(".disabled"):
            # Extract base name
            base_name = name.rsplit(".disabled", 1)[0]
            if base_name not in hooks_status:
                hooks_status[base_name] = {
                    "status": "disabled",
                    "last_ran_at": None,
                }
            continue

        if not name.endswith(".sh"):
            continue

        base_name = name[:-3]  # Remove .sh

        # Check if disabled version exists
        disabled_path = item.parent / f"{name}.disabled"
        status = "disabled" if disabled_path.exists() else "enabled"

        if base_name not in hooks_status:
            hooks_status[base_name] = {
                "status": status,
                "last_ran_at": None,
            }
        else:
            hooks_status[base_name]["status"] = status

    return hooks_status


def insert_hook_status(db, hooks_status: dict[str, dict]) -> None:
    """Insert or update hook status in database."""
    timestamp = datetime.utcnow().isoformat()
    try:
        with db:
            for hook_name, status_info in hooks_status.items():
                db.execute(
                    """
                    INSERT OR REPLACE INTO hook_executions
                    (timestamp, hook_name, status, success_count, error_count, last_ran_at)
                    VALUES (?, ?, ?, 0, 0, ?)
                    """,
                    (timestamp, hook_name, status_info["status"], status_info["last_ran_at"]),
                )
    except Exception as e:
        log.error("Failed to insert hook status: %s", e)


async def run_hook_monitor() -> None:
    """Background task: scan hook directory every 30 seconds."""
    log.info("Starting hook monitor (30s interval)")
    try:
        while True:
            try:
                hooks_status = scan_hook_directory()
                if hooks_status:
                    db = get_db()
                    insert_hook_status(db, hooks_status)
                    db.close()
            except Exception as e:
                log.error("Error in hook monitor loop: %s", e)

            await asyncio.sleep(30)
    except asyncio.CancelledError:
        log.info("Hook monitor cancelled")
