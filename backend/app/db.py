"""SQLite connection helpers. WAL mode, one connection per caller."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import DB_PATH

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_init_lock = threading.Lock()
_initialised = False


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.row_factory = sqlite3.Row


# schema.sql is all CREATE TABLE IF NOT EXISTS, so it cannot add a column to a
# table that already exists. Additive columns go here instead of in another
# one-off migrate script.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("messages", "cache_create_1h_tokens", "INTEGER DEFAULT 0"),
    ("messages", "request_id", "TEXT"),
)

# Indexes on columns added above must be created after the column exists, so
# they can't live in schema.sql (which runs first).
_POST_MIGRATION_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_messages_request ON messages(request_id) WHERE request_id IS NOT NULL",
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing or column in existing:
            continue  # table absent (schema.sql just made it) or column already there
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    """Apply schema.sql, then any additive column migrations. Idempotent."""
    global _initialised
    with _init_lock:
        if _initialised:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        try:
            _configure(conn)
            ddl = _SCHEMA_PATH.read_text()
            conn.executescript(ddl)
            _add_missing_columns(conn)
            for stmt in _POST_MIGRATION_DDL:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()
        _initialised = True


def connect() -> sqlite3.Connection:
    """Return a fresh connection. Caller owns close()."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    _configure(conn)
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


@contextmanager
def readonly_cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
    finally:
        conn.close()


def kv_get(key: str) -> str | None:
    with readonly_cursor() as cur:
        row = cur.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO kv(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
