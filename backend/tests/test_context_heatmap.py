"""Regression test for /api/context-heatmap's 'current context size' calc.

The query used to read `ORDER BY ts DESC LIMIT 1` after a bare SUM() with no
GROUP BY. That ordering/limiting is a no-op on an aggregate query — SUM
without GROUP BY always returns exactly one row — so the endpoint summed every
assistant message in the session instead of reading the last one. A session
with several turns could read many multiples of its real context size.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.db import connect
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture
def live_session():
    sid = "ctx-test-" + uuid.uuid4().hex[:8]
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO sessions(id, project_hash, cwd, model, last_modified_ts, outcome)
            VALUES (?, 'h', '/tmp', 'claude-opus-5', ?, 'ok')
            """,
            (sid, time.time()),
        )
        # Three assistant turns. If the endpoint summed instead of reading the
        # last row, current_tokens would be 300 + 3000 + 30000 = 33,300
        # instead of the last turn's own 30,000.
        for i, tokens in enumerate([300, 3_000, 30_000]):
            conn.execute(
                """
                INSERT INTO messages(uuid, session_id, role, model, ts, input_tokens)
                VALUES (?, ?, 'assistant', 'claude-opus-5', ?, ?)
                """,
                (f"m-{sid}-{i}", sid, f"2026-01-01T00:0{i}:00Z", tokens),
            )
        conn.commit()
        yield sid
    finally:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()
        conn.close()


def test_current_tokens_is_last_message_not_session_sum(live_session) -> None:
    r = client.get("/api/context-heatmap")
    assert r.status_code == 200
    rows = {s["session_id"]: s for s in r.json()["sessions"]}
    assert live_session in rows
    assert rows[live_session]["current_tokens"] == 30_000


def test_context_heatmap_handles_session_with_no_assistant_message() -> None:
    """A session can go live with only a user turn so far — no assistant
    message yet to read a token count from. Must return 0, not crash."""
    sid = "ctx-test-empty-" + uuid.uuid4().hex[:8]
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO sessions(id, project_hash, cwd, model, last_modified_ts, outcome)
            VALUES (?, 'h', '/tmp', 'claude-opus-5', ?, 'ok')
            """,
            (sid, time.time()),
        )
        conn.commit()
        r = client.get("/api/context-heatmap")
        assert r.status_code == 200
        rows = {s["session_id"]: s for s in r.json()["sessions"]}
        assert rows[sid]["current_tokens"] == 0
        assert rows[sid]["pct"] == 0
    finally:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()
        conn.close()
