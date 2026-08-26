"""Monitor hook status (enabled/disabled) from ~/.claude/hooks/."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
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
    """Upsert current hook status, one row per hook.

    This used to write `INSERT OR REPLACE INTO hook_executions` keyed on
    (timestamp, hook_name) with a timestamp regenerated on every 30-second
    tick — so the REPLACE never fired and the table grew without bound. Keying
    on hook_name alone makes the upsert real and caps the table at the number
    of hook scripts.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with db:
            for hook_name, status_info in hooks_status.items():
                db.execute(
                    """
                    INSERT INTO hook_status
                        (hook_name, status, success_count, error_count,
                         last_ran_at, updated_at)
                    VALUES (?, ?, 0, 0, ?, ?)
                    ON CONFLICT(hook_name) DO UPDATE SET
                        status      = excluded.status,
                        last_ran_at = COALESCE(excluded.last_ran_at, hook_status.last_ran_at),
                        updated_at  = excluded.updated_at
                    """,
                    (hook_name, status_info["status"], status_info["last_ran_at"], timestamp),
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
