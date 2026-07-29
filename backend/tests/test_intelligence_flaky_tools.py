"""Regression tests for the /api/intelligence flaky-tool check.

Two real bugs found by hand-auditing this card against dashboard.db (not
hypotheticals):

1. No time window — an all-time scan kept re-flagging a playwright issue
   (missing Chrome binary) that a system symlink fix resolved on 2026-05-26
   and that never recurred, months after it stopped being true.
2. is_error=1 includes the harness's own "user declined this tool call" /
   "permission denied" rows, which are normal interactive use, not a tool
   defect. AskUserQuestion's "37.5% failure rate" was 110 of 113 "errors"
   being the user answering the question, not the tool breaking.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db import connect
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

_DECLINE_MSG = (
    "The user doesn't want to proceed with this tool use. "
    "The tool use was rejected."
)
_PERMISSION_MSG = "Permission for this tool use was denied. The tool call was blocked."
# A real error whose tool OUTPUT happens to contain the substring "rejected" —
# must NOT be caught by a loose `LIKE '%rejected%'` decline filter, only by
# one anchored on the harness's actual fixed-prefix message.
_GIT_REJECT_MSG = "! [rejected] main -> main (non-fast-forward)"


def _insert_tool_call(conn, session_id, tool_name, ts, is_error, error_preview=None):
    conn.execute(
        """
        INSERT INTO tool_calls(tool_use_id, session_id, tool_name, started_at,
                                ended_at, duration_ms, is_error, error_preview)
        VALUES (?, ?, ?, ?, ?, 100, ?, ?)
        """,
        (f"tu-{uuid.uuid4().hex[:12]}", session_id, tool_name, ts, ts, is_error, error_preview),
    )


@pytest.fixture
def sample_session():
    sid = "flaky-test-" + uuid.uuid4().hex[:8]
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id, project_hash, cwd) VALUES (?, 'h', '/tmp')",
            (sid,),
        )
        conn.commit()
        yield conn, sid
    finally:
        conn.execute("DELETE FROM tool_calls WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()
        conn.close()


def test_stale_flaky_tool_does_not_resurface(sample_session) -> None:
    """A tool that only failed >14 days ago must not be flagged as flaky now —
    that's exactly the playwright/chrome-path bug: fixed 2026-05-26, still
    showing as broken on every load because the query never looked at when
    the errors happened."""
    conn, sid = sample_session
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).isoformat()
    for i in range(60):
        _insert_tool_call(
            conn, sid, "StaleTool", old, is_error=1, error_preview="genuinely broken"
        )
    conn.commit()

    r = client.get("/api/intelligence")
    assert r.status_code == 200
    ids = [rec["id"] for rec in r.json()["recommendations"]]
    assert "flaky-tool-StaleTool" not in ids


def test_declined_tool_calls_are_not_counted_as_flaky(sample_session) -> None:
    """A tool with a genuinely low error rate, once you exclude user declines,
    must not be flagged — even if is_error=1 was set on every declined row."""
    conn, sid = sample_session
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(60):
        _insert_tool_call(conn, sid, "PolitelyDeclinedTool", now, is_error=1, error_preview=_DECLINE_MSG)
    for i in range(5):
        _insert_tool_call(conn, sid, "PolitelyDeclinedTool", now, is_error=1, error_preview=_PERMISSION_MSG)
    for i in range(55):
        _insert_tool_call(conn, sid, "PolitelyDeclinedTool", now, is_error=0)
    conn.commit()

    r = client.get("/api/intelligence")
    ids = [rec["id"] for rec in r.json()["recommendations"]]
    assert "flaky-tool-PolitelyDeclinedTool" not in ids


def test_git_style_rejected_output_still_counts_as_a_real_error(sample_session) -> None:
    """A tool whose OUTPUT happens to contain the word "rejected" (a git push
    rejection, not a user declining the tool call) must still count toward
    the error rate — the decline filter is anchored on the harness's fixed
    message prefix, not a loose substring match."""
    conn, sid = sample_session
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(60):
        _insert_tool_call(conn, sid, "GitPushingTool", now, is_error=1, error_preview=_GIT_REJECT_MSG)
    for i in range(20):
        _insert_tool_call(conn, sid, "GitPushingTool", now, is_error=0)
    conn.commit()

    r = client.get("/api/intelligence")
    ids = [rec["id"] for rec in r.json()["recommendations"]]
    assert "flaky-tool-GitPushingTool" in ids


def test_genuinely_flaky_tool_within_window_is_still_flagged(sample_session) -> None:
    conn, sid = sample_session
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(60):
        _insert_tool_call(conn, sid, "ActuallyBrokenTool", now, is_error=1, error_preview="schema mismatch")
    for i in range(20):
        _insert_tool_call(conn, sid, "ActuallyBrokenTool", now, is_error=0)
    conn.commit()

    r = client.get("/api/intelligence")
    ids = [rec["id"] for rec in r.json()["recommendations"]]
    assert "flaky-tool-ActuallyBrokenTool" in ids
