#!/usr/bin/env python3
"""Drop the superseded hook_executions table and reclaim its space.

hook_executions was never meant to be a history log. Its PRIMARY KEY included
timestamp and the writer generated a fresh one every 30-second tick, so
`INSERT OR REPLACE` never replaced — it appended one row per hook per tick for
the life of the service. The table plus its index reached roughly 589MB of a
1.17GB database to answer a question about ~28 hooks.

hook_status replaced it. Nothing reads hook_executions any more; verify with
`grep -rn hook_executions backend/ scripts/` before running.

THIS DESTROYS DATA. A full-database backup is written with VACUUM INTO before
anything is dropped, and the script refuses to continue if that fails.

Restore, if it ever turns out something wanted this history:
    systemctl --user stop claude-dashboard
    cp <backup path printed below> data/dashboard.db
    systemctl --user start claude-dashboard

The server holds a write lock and VACUUM needs an uncontended one. Stop it:
    systemctl --user stop claude-dashboard

Usage:
    python3 scripts/drop_hook_executions.py --dry-run   # report only
    python3 scripts/drop_hook_executions.py             # apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import DB_PATH  # noqa: E402


def _mib(n: int) -> str:
    return f"{n / (1024 * 1024):,.0f}MiB"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000;")

    present = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hook_executions'"
    ).fetchone()
    if not present:
        print("hook_executions is already gone, nothing to do")
        conn.close()
        return 0

    rows = conn.execute("SELECT COUNT(*) FROM hook_executions").fetchone()[0]
    size_before = DB_PATH.stat().st_size
    print(f"  hook_executions: {rows:,} rows")
    print(f"  database: {_mib(size_before)}")

    if args.dry_run:
        print("dry run, nothing written")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DB_PATH.parent / f"dashboard.db.bak-hook_executions-{stamp}"
    print(f"  backing up to {backup} ...")
    # VACUUM INTO writes a consistent copy of the whole database, so the restore
    # path is a file copy rather than a table reconstruction.
    conn.execute("VACUUM INTO ?", (str(backup),))
    if not backup.exists() or backup.stat().st_size == 0:
        print("ERROR: backup missing or empty, refusing to drop", file=sys.stderr)
        conn.close()
        return 1
    print(f"  backup written, {_mib(backup.stat().st_size)}")

    conn.execute("DROP INDEX IF EXISTS idx_hook_exec_recent")
    conn.execute("DROP TABLE hook_executions")
    conn.commit()
    # VACUUM cannot run inside a transaction; sqlite3 opens one implicitly for
    # DDL, so the commit above is required before this line.
    conn.execute("VACUUM")
    conn.close()

    size_after = DB_PATH.stat().st_size
    print(f"  database: {_mib(size_before)} -> {_mib(size_after)} "
          f"(reclaimed {_mib(size_before - size_after)})")
    print("done. restart: systemctl --user start claude-dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
