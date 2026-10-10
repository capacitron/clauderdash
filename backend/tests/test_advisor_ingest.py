"""Advisor spend: the Opus advisor bills inside the executor's API call.

Its tokens arrive only in `usage.iterations` as a `type: "advisor_message"`
entry carrying its own model. The top-level usage is the executor's alone
(input 4 = 2+2, output 344 = 26+318 below), so the advisor is not double
counted by the executor row; before this fix it was not counted at all.

Fixture: the pilot request req_011CftWGYZfTekkr6inpoDsZ, 2026-10-10, written
as three sibling lines that repeat the identical usage object.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.ingest.jsonl import _handle_assistant
from app.ingest.pricing import get_cost_usd

_SCHEMA = Path(__file__).resolve().parent.parent / "app" / "schema.sql"

REQ = "req_011CftWGYZfTekkr6inpoDsZ"
USAGE = {
    "input_tokens": 4,
    "cache_creation_input_tokens": 40800,
    "cache_read_input_tokens": 62057,
    "output_tokens": 344,
    "cache_creation": {"ephemeral_1h_input_tokens": 40103, "ephemeral_5m_input_tokens": 0},
    "iterations": [
        {"input_tokens": 2, "output_tokens": 26, "cache_read_input_tokens": 10977,
         "cache_creation_input_tokens": 40103,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 40103},
         "type": "message"},
        {"input_tokens": 52661, "output_tokens": 1669, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
         "type": "advisor_message", "model": "claude-opus-5-5"},
        {"input_tokens": 2, "output_tokens": 318, "cache_read_input_tokens": 51080,
         "cache_creation_input_tokens": 697,
         "cache_creation": {"ephemeral_5m_input_tokens": 697, "ephemeral_1h_input_tokens": 0},
         "type": "message"},
    ],
}
SIBLINGS = [
    ("bca76cbb-dd9a-435a-9ae5-a6209ef04246", "2026-10-10T11:49:33.937Z"),
    ("050467d9-5d25-4d34-b59f-ac3d97202d56", "2026-10-10T11:49:56.771Z"),
    ("da9b6cd1-bcf4-43e4-9b26-16103275acf3", "2026-10-10T11:49:56.781Z"),
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA.read_text())
    conn.execute("INSERT INTO sessions(id, project_hash) VALUES('s1', 'proj')")
    return conn


def _event(uuid, ts, usage=USAGE, req=REQ):
    return {"uuid": uuid, "timestamp": ts, "requestId": req,
            "message": {"model": "claude-sonnet-5-5", "usage": usage, "content": []}}


def _ingest(conn, events):
    for e in events:
        _handle_assistant(conn, e, "s1")


def _advisor_rows(conn):
    return conn.execute(
        "SELECT uuid, model, input_tokens, output_tokens, cost_usd, parent_uuid "
        "FROM messages WHERE request_id = ?", (REQ + "#advisor",)
    ).fetchall()


def _session_cost(conn):
    return conn.execute("SELECT cost_usd FROM sessions WHERE id='s1'").fetchone()[0]


def test_one_advisor_row_across_siblings():
    conn = _conn()
    _ingest(conn, [_event(u, t) for u, t in SIBLINGS])

    rows = _advisor_rows(conn)
    assert len(rows) == 1
    uuid, model, inp, out, cost, parent = rows[0]
    assert uuid == REQ + "#advisor1"
    assert (model, inp, out) == ("claude-opus-5-5", 52661, 1669)
    assert parent == SIBLINGS[0][0]
    assert cost == get_cost_usd("claude-opus-5-5", 52661, 1669, 0, 0, 0)

    # Executor row still billed once, unchanged.
    billed = conn.execute(
        "SELECT model, cost_usd FROM messages WHERE request_id = ? AND output_tokens > 0", (REQ,)
    ).fetchall()
    assert len(billed) == 1 and billed[0][0] == "claude-sonnet-5-5"
    assert abs(_session_cost(conn) - (billed[0][1] + cost)) < 1e-12


def test_replay_adds_nothing():
    conn = _conn()
    events = [_event(u, t) for u, t in SIBLINGS]
    _ingest(conn, events)
    before = (_session_cost(conn), conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    _ingest(conn, events)
    after = (_session_cost(conn), conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    assert before == after


def test_no_iterations_is_unchanged():
    conn = _conn()
    usage = {k: v for k, v in USAGE.items() if k != "iterations"}
    _ingest(conn, [_event(SIBLINGS[0][0], SIBLINGS[0][1], usage=usage)])
    assert _advisor_rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_no_request_id_skips_advisor():
    conn = _conn()
    e = _event(SIBLINGS[0][0], SIBLINGS[0][1], req=None)
    _ingest(conn, [e])
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE uuid LIKE '%#advisor%'").fetchone()[0] == 0
