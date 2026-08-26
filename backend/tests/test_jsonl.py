"""_upsert_session: started_at must be healable but never re-written.

started_at was in the INSERT column list and absent from the ON CONFLICT SET
clause entirely, so a session whose first-seen event carried no parseable
timestamp had started_at pinned NULL forever. No later event could fill it.

That is not cosmetic: yield_analyzer's pending-session query filters on
`started_at IS NOT NULL`, so those sessions were permanently invisible to
outcome attribution rather than merely unprocessed. 934 sessions were in that
state, 933 of them entrypoint='cli' — the plain-CLI transcript's opening line
lacks the timestamp field the SDK entrypoint always writes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.ingest.jsonl import _upsert_session

_SCHEMA = Path(__file__).resolve().parent.parent / "app" / "schema.sql"

EARLY = "2026-01-01T10:00:00.000Z"
LATER = "2026-01-01T18:30:00.000Z"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA.read_text())
    return conn


def _upsert(conn, ts, *, sid="s1", model="claude-opus-5", mtime=1.0):
    _upsert_session(conn, sid, "proj", "/tmp/wd", model, ts, "cli", "2.0", mtime)


def _started_at(conn, sid="s1"):
    return conn.execute("SELECT started_at FROM sessions WHERE id=?", (sid,)).fetchone()[0]


def test_null_started_at_is_healed_by_a_later_timestamped_event():
    """The bug. First event has no timestamp; the next one must fill it."""
    conn = _conn()
    _upsert(conn, None)
    assert _started_at(conn) is None

    _upsert(conn, EARLY, mtime=2.0)

    assert _started_at(conn) == EARLY


def test_established_started_at_is_never_overwritten():
    """Healing must not turn into last-write-wins — started_at is the session's
    beginning, so the first non-null value is the correct one forever."""
    conn = _conn()
    _upsert(conn, EARLY)
    _upsert(conn, LATER, mtime=2.0)

    assert _started_at(conn) == EARLY


def test_a_later_null_does_not_erase_started_at():
    conn = _conn()
    _upsert(conn, EARLY)
    _upsert(conn, None, mtime=2.0)

    assert _started_at(conn) == EARLY


def test_other_columns_keep_prefer_incoming_semantics():
    """Characterization: started_at is deliberately the odd one out. Every
    other column takes the incoming value when it has one."""
    conn = _conn()
    _upsert(conn, EARLY, model="claude-sonnet-5")
    _upsert(conn, LATER, model="claude-opus-5", mtime=2.0)

    row = conn.execute(
        "SELECT model, last_event_at, last_modified_ts FROM sessions WHERE id='s1'"
    ).fetchone()

    assert row[0] == "claude-opus-5"
    assert row[1] == LATER
    assert row[2] == 2.0
