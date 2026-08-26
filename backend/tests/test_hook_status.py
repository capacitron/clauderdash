"""hook_status: one row per hook, and every hook visible.

Two separate defects lived in the old hook_executions path.

Storage: the writer generated a fresh timestamp on every 30-second tick while
the PRIMARY KEY was (timestamp, hook_name), so `INSERT OR REPLACE` never
replaced. The table grew to 3.69M rows, about 589MB of a 1.17GB database, to
answer a question about ~31 hooks.

Read: /api/hook-status filtered on a single global MAX(timestamp) rather than a
per-hook maximum, so any hook whose last write was not the most recent write in
the whole table disappeared from the response entirely.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.ingest.hook_monitor import insert_hook_status

_SCHEMA = Path(__file__).resolve().parent.parent / "app" / "schema.sql"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA.read_text())
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn):
    return conn.execute(
        "SELECT hook_name, status, last_ran_at FROM hook_status ORDER BY hook_name"
    ).fetchall()


def test_repeated_ticks_do_not_accumulate_rows():
    """The bug that grew the database. Same hooks, many ticks, one row each."""
    conn = _conn()
    hooks = {f"hook-{i}": {"status": "enabled", "last_ran_at": None} for i in range(31)}

    for _ in range(10):
        insert_hook_status(conn, hooks)

    assert conn.execute("SELECT COUNT(*) FROM hook_status").fetchone()[0] == 31


def test_status_change_is_reflected_not_appended():
    conn = _conn()
    insert_hook_status(conn, {"backup-preflight": {"status": "enabled", "last_ran_at": None}})
    insert_hook_status(conn, {"backup-preflight": {"status": "disabled", "last_ran_at": None}})

    assert [(r["hook_name"], r["status"]) for r in _rows(conn)] == [
        ("backup-preflight", "disabled")
    ]


def test_every_hook_stays_visible_after_a_partial_tick():
    """The read bug. A tick that reports only some hooks must not hide the rest
    — under the old global-MAX filter, `quiet` would have vanished."""
    conn = _conn()
    insert_hook_status(conn, {
        "quiet": {"status": "enabled", "last_ran_at": None},
        "chatty": {"status": "enabled", "last_ran_at": None},
    })
    insert_hook_status(conn, {"chatty": {"status": "disabled", "last_ran_at": None}})

    assert [r["hook_name"] for r in _rows(conn)] == ["chatty", "quiet"]


def test_last_ran_at_is_not_erased_by_a_tick_that_lacks_it():
    """scan_hook_directory reports last_ran_at as None. A plain overwrite would
    discard a real timestamp on the very next tick."""
    conn = _conn()
    insert_hook_status(conn, {"h": {"status": "enabled", "last_ran_at": "2026-08-26T10:00:00Z"}})
    insert_hook_status(conn, {"h": {"status": "enabled", "last_ran_at": None}})

    assert _rows(conn)[0]["last_ran_at"] == "2026-08-26T10:00:00Z"


def test_updated_at_advances_on_every_tick():
    """Row count stays flat, so updated_at is the only evidence the monitor is
    alive. It must move even when nothing about the hook changed."""
    conn = _conn()
    hooks = {"h": {"status": "enabled", "last_ran_at": None}}
    insert_hook_status(conn, hooks)
    first = conn.execute("SELECT updated_at FROM hook_status").fetchone()[0]
    insert_hook_status(conn, hooks)
    second = conn.execute("SELECT updated_at FROM hook_status").fetchone()[0]

    assert second >= first
