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


def init_db() -> None:
    """Apply schema.sql. Idempotent."""
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
