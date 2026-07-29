"""/api/agent-usage: cost attribution by subagent type.

Direct per-message attribution via messages.agent_type, stamped at ingest from
each subagent transcript's `.meta.json` sidecar (see
app/ingest/jsonl.py::_read_agent_type and scripts/migrate_v3_agent_type.py for
the historical backfill).
"""
from __future__ import annotations

import datetime as dt
import time
import uuid

import pytest

from app.db import connect
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture
def attributed_messages():
    """One main-loop message ($1) + two subagent messages ($2 Explore, $1
    general-purpose) in the same session. Total spend $4, subagent spend $3."""
    sid = "agent-test-" + uuid.uuid4().hex[:8]
    conn = connect()
    uuids = []
    try:
        conn.execute(
            """
            INSERT INTO sessions(id, project_hash, cwd, model, last_modified_ts, outcome)
            VALUES (?, 'h', '/tmp', 'claude-sonnet-5', ?, 'ok')
            """,
            (sid, time.time()),
        )
        rows = [
            (None, 1.0),
            ("Explore", 2.0),
            ("general-purpose", 1.0),
        ]
        now = dt.datetime.now(dt.timezone.utc)
        for i, (agent_type, cost) in enumerate(rows):
            muuid = f"m-{sid}-{i}"
            uuids.append(muuid)
            ts = (now - dt.timedelta(minutes=i)).isoformat(timespec="seconds").replace("+00:00", "Z")
            conn.execute(
                """
                INSERT INTO messages(uuid, session_id, role, model, ts, cost_usd, agent_type)
                VALUES (?, ?, 'assistant', 'claude-sonnet-5', ?, ?, ?)
                """,
                (muuid, sid, ts, cost, agent_type),
            )
        conn.commit()
        yield sid
    finally:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()
        conn.close()


def test_agent_usage_attributes_cost_by_type(attributed_messages) -> None:
    r = client.get("/api/agent-usage?range=30d")
    assert r.status_code == 200
    data = r.json()
    by_type = {a["agent_type"]: a for a in data["agents"]}

    assert by_type["Explore"]["cost_usd"] == pytest.approx(2.0)
    assert by_type["Explore"]["runs"] == 1
    assert by_type["general-purpose"]["cost_usd"] == pytest.approx(1.0)
    # The main-loop message (agent_type IS NULL) must not appear as a bucket.
    assert None not in by_type
    assert "null" not in by_type


def test_agent_usage_cost_share_is_relative_to_all_spend(attributed_messages) -> None:
    """cost_share is measured against ALL spend (main-loop + subagent) in the
    window, not against subagent spend alone. DEV_MODE seeds unrelated fixture
    cost into the same 30d window, so this checks the endpoint's own arithmetic
    (cost_share == cost_usd / total_cost_usd) rather than a hardcoded ratio —
    the fixture's $1 main-loop message alone rules out "share of subagent
    spend only" as the actual implementation.
    """
    r = client.get("/api/agent-usage?range=30d")
    data = r.json()
    by_type = {a["agent_type"]: a for a in data["agents"]}
    explore = by_type["Explore"]
    # abs, not rel: cost_usd and total_cost_usd are both rounded to 4dp before
    # being returned, so recomputing the ratio from them only approximates the
    # unrounded cost_share the endpoint actually returns — a tight relative
    # tolerance flakes whenever DEV_MODE's randomised fixture total is small.
    assert explore["cost_share"] == pytest.approx(
        explore["cost_usd"] / data["total_cost_usd"], abs=1e-4
    )
    # Sanity: with the $1 main-loop message counted in the denominator,
    # Explore's $2 must be strictly less than 2/3 (its share of subagent-only
    # spend) — pins the denominator to total spend, not agent_cost_usd.
    assert explore["cost_share"] < 2.0 / 3.0


def test_agent_usage_share_is_bounded_when_no_subagent_rows() -> None:
    """total_cost_usd can be 0 for a narrow window — must not divide by zero."""
    r = client.get("/api/agent-usage?range=today")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["agents"], list)
    assert 0.0 <= data["agent_cost_share"] <= 1.0
