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


def test_advisor_iterations_were_ignored():
    """Characterization of the pre-fix ledger: one usage-bearing executor row,
    no Opus row anywhere."""
    conn = _conn()
    _ingest(conn, [_event(u, t) for u, t in SIBLINGS])

    billed = conn.execute(
        "SELECT model FROM messages WHERE input_tokens > 0 OR output_tokens > 0"
    ).fetchall()
    assert billed == [("claude-sonnet-5-5",)]
    assert conn.execute(
        "SELECT COUNT(*) FROM messages WHERE model LIKE 'claude-opus%'"
    ).fetchone()[0] == 0
