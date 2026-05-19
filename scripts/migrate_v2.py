#!/usr/bin/env python3
"""v2 schema migration. Safe to run multiple times (idempotent).

Adds: sessions.provider, sessions.cost_usd, sessions.activity_type,
      sessions.one_shot, sessions.yielded, sessions.yield_commit,
      messages.cost_usd, waste_findings table.

Then backfills cost_usd on existing messages and sessions using hardcoded
fallback pricing (LiteLLM fetch happens at runtime).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Resolve DB path — same logic as config.py
_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(
    __import__("os").environ.get(
        "CLAUDE_DASHBOARD_DATA_DIR", _ROOT / "data"
    )
)
DB_PATH = _DATA_DIR / "dashboard.db"

# Fallback pricing per token (matches pricing.py _FALLBACK)
_FALLBACK: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"input": 15e-6, "output": 75e-6,  "cache_write": 18.75e-6, "cache_read": 1.5e-6},
    "claude-opus-4-6":   {"input": 15e-6, "output": 75e-6,  "cache_write": 18.75e-6, "cache_read": 1.5e-6},
    "claude-opus-4-5":   {"input": 15e-6, "output": 75e-6,  "cache_write": 18.75e-6, "cache_read": 1.5e-6},
    "claude-sonnet-4-6": {"input": 3e-6,  "output": 15e-6,  "cache_write": 3.75e-6,  "cache_read": 0.3e-6},
    "claude-sonnet-4-5": {"input": 3e-6,  "output": 15e-6,  "cache_write": 3.75e-6,  "cache_read": 0.3e-6},
    "claude-haiku-4-5":  {"input": 0.8e-6,"output": 4e-6,   "cache_write": 1e-6,     "cache_read": 0.08e-6},
}


def _cost(model: str | None, inp: int, out: int, cr: int, cw: int) -> float:
    if not model:
        model = "claude-sonnet-4-6"
    for key, p in _FALLBACK.items():
        if key in model:
            return inp * p["input"] + out * p["output"] + cr * p["cache_read"] + cw * p["cache_write"]
    # Unknown model → sonnet fallback
    p = _FALLBACK["claude-sonnet-4-6"]
    return inp * p["input"] + out * p["output"] + cr * p["cache_read"] + cw * p["cache_write"]


def _add_column(conn: sqlite3.Connection, table: str, col: str, definition: str) -> bool:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if col in existing:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    print(f"  + {table}.{col}")
    return True


def _create_table_if_missing(conn: sqlite3.Connection, ddl: str, table: str) -> None:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        conn.executescript(ddl)
        print(f"  + table {table}")


def run() -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH} — nothing to migrate.")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")

    print("=== v2 migration ===")

    # 1. Add new columns (idempotent)
    _add_column(conn, "sessions", "provider",      "TEXT DEFAULT 'claude-code'")
    _add_column(conn, "sessions", "cost_usd",      "REAL DEFAULT 0")
    _add_column(conn, "sessions", "activity_type", "TEXT DEFAULT 'other'")
    _add_column(conn, "sessions", "one_shot",      "INTEGER DEFAULT 0")
    _add_column(conn, "sessions", "yielded",       "INTEGER")
    _add_column(conn, "sessions", "yield_commit",  "TEXT")
    _add_column(conn, "messages", "cost_usd",      "REAL DEFAULT 0")

    # 2. waste_findings table
    _create_table_if_missing(conn, """
        CREATE TABLE IF NOT EXISTS waste_findings (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id  TEXT,
          pattern     TEXT NOT NULL,
          severity    TEXT NOT NULL,
          detail      TEXT,
          tokens_saved INTEGER DEFAULT 0,
          cost_impact REAL DEFAULT 0,
          fix_label   TEXT,
          fix_text    TEXT,
          detected_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_waste_pattern ON waste_findings(pattern, detected_at DESC);
    """, "waste_findings")

    conn.commit()

    # 3. Backfill messages.cost_usd where 0 and tokens exist
    print("Backfilling messages.cost_usd …")
    rows = conn.execute(
        """
        SELECT uuid, model, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens
          FROM messages
         WHERE role = 'assistant'
           AND (cost_usd IS NULL OR cost_usd = 0)
           AND (input_tokens > 0 OR output_tokens > 0)
        """
    ).fetchall()

    updated = 0
    for row in rows:
        cost = _cost(
            row["model"],
            int(row["input_tokens"] or 0),
            int(row["output_tokens"] or 0),
            int(row["cache_read_tokens"] or 0),
            int(row["cache_create_tokens"] or 0),
        )
        if cost > 0:
            conn.execute("UPDATE messages SET cost_usd = ? WHERE uuid = ?", (cost, row["uuid"]))
            updated += 1

    conn.commit()
    print(f"  Updated {updated} messages")

    # 4. Backfill sessions.cost_usd from messages
    print("Backfilling sessions.cost_usd …")
    conn.execute("""
        UPDATE sessions
           SET cost_usd = (
               SELECT COALESCE(SUM(m.cost_usd), 0)
                 FROM messages m
                WHERE m.session_id = sessions.id
           )
         WHERE cost_usd = 0 OR cost_usd IS NULL
    """)
    conn.commit()
    session_count = conn.execute("SELECT COUNT(*) FROM sessions WHERE cost_usd > 0").fetchone()[0]
    print(f"  {session_count} sessions have cost_usd > 0")

    # 5. Set provider for existing sessions
    conn.execute("UPDATE sessions SET provider = 'claude-code' WHERE provider IS NULL")
    conn.commit()

    conn.close()
    print("=== migration complete ===")


if __name__ == "__main__":
    run()
