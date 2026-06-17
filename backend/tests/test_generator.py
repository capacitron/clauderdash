"""Drift engine tests for scripts/generate_recommendations.py.

The generator is a standalone script (no app import). We add its dir to
sys.path and import the pure functions directly. All DB work is in-memory.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import generate_recommendations as gen  # noqa: E402


# ---------------------------------------------------------------- helpers
def _seed_db() -> sqlite3.Connection:
    """In-memory DB with just enough schema + rows to exercise the checks."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE messages (
            session_id TEXT, role TEXT, model TEXT, ts TEXT,
            input_tokens INT, output_tokens INT,
            cache_read_tokens INT, cache_create_tokens INT
        );
        CREATE TABLE sessions (id TEXT, outcome TEXT, started_at TEXT);
        CREATE TABLE tool_calls (
            session_id TEXT, tool_name TEXT, is_error INT,
            error_preview TEXT, started_at TEXT
        );
        CREATE TABLE skill_invocations (id INTEGER PRIMARY KEY, session_id TEXT,
            skill_name TEXT, ts TEXT);
        """
    )
    now = "datetime('now')"
    # 80% opus tokens (8000 of 10000) → opus_share should breach <20%
    conn.execute(
        f"INSERT INTO messages VALUES ('s1','assistant','claude-opus-4-8',{now},"
        "8000,0,0,0)"
    )
    conn.execute(
        f"INSERT INTO messages VALUES ('s1','assistant','claude-haiku-4-5',{now},"
        "2000,0,0,0)"
    )
    return conn


def _write_targets_table(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "CLAUDE.md"
    f.write_text(
        "# Test\n\n## Measurable Targets (Drift-Checked)\n\n"
        "| id | Target | Window | Threshold |\n"
        "|----|--------|--------|-----------|\n" + body
    )
    return f


# ---------------------------------------------------------------- parser
def test_parse_drift_targets_reads_table(tmp_path):
    f = _write_targets_table(
        tmp_path,
        "| opus_share | Opus share | 30d | < 20% |\n"
        "| cache_hit | Cache hit | 30d | >= 70% |\n",
    )
    targets = gen.parse_drift_targets(f)
    assert set(targets) == {"opus_share", "cache_hit"}
    assert targets["opus_share"]["op"] == "<"
    assert targets["opus_share"]["threshold"] == 20.0
    assert targets["cache_hit"]["op"] == ">="


def test_parse_drift_targets_missing_file_is_empty(tmp_path):
    assert gen.parse_drift_targets(tmp_path / "nope.md") == {}


def test_parse_drift_targets_ignores_other_sections(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text(
        "## Other\n\n| id | x | y | < 5% |\n\n"
        "## Measurable Targets (Drift-Checked)\n\n"
        "| id | Target | Window | Threshold |\n"
        "|----|--------|--------|-----------|\n"
        "| opus_share | Opus | 30d | < 20% |\n"
    )
    targets = gen.parse_drift_targets(f)
    assert list(targets) == ["opus_share"]


# ---------------------------------------------------------------- satisfies
@pytest.mark.parametrize("actual,op,thr,ok", [
    (15, "<", 20, True), (25, "<", 20, False),
    (70, ">=", 70, True), (69, ">=", 70, False),
    (2, "<", 3, True), (3, "<", 3, False),
])
def test_satisfies(actual, op, thr, ok):
    assert gen._satisfies(actual, op, thr) is ok


# ---------------------------------------------------------------- compute
def test_compute_drift_flags_opus_overspend(tmp_path):
    conn = _seed_db()
    f = _write_targets_table(tmp_path, "| opus_share | Opus share | 30d | < 20% |\n")
    recs = gen.compute_drift(conn, f)
    assert len(recs) == 1
    r = recs[0]
    assert r["rule_ref"] == "CLAUDE.md:opus_share"
    assert r["actual"] == "80%"           # 8000/10000
    assert r["drift_score"] == 1.0
    assert r["tier"] == "high"
    assert r["confidence"] == 1.0
    # evidence_sql must be a runnable SELECT
    assert r["evidence_sql"].lstrip().upper().startswith("SELECT")
    conn.execute(r["evidence_sql"]).fetchone()


def test_compute_drift_silent_when_compliant(tmp_path):
    conn = _seed_db()
    # Threshold raised above the measured 80% → compliant → no drift
    f = _write_targets_table(tmp_path, "| opus_share | Opus share | 30d | < 90% |\n")
    assert gen.compute_drift(conn, f) == []


def test_compute_drift_null_actual_skipped(tmp_path):
    conn = _seed_db()  # no sessions rows → fail_rate ratio is NULL
    f = _write_targets_table(tmp_path, "| fail_rate | Fail rate | 30d | < 10% |\n")
    # NULL actual (no data in window) → skipped, never a false drift claim
    assert gen.compute_drift(conn, f) == []


def test_compute_drift_only_declared_rules_run(tmp_path):
    conn = _seed_db()  # opus data present
    # opus_share NOT declared → not checked even though data would breach it
    f = _write_targets_table(tmp_path, "| fail_rate | Fail rate | 30d | < 10% |\n")
    recs = gen.compute_drift(conn, f)
    assert all(r["rule_ref"] != "CLAUDE.md:opus_share" for r in recs)


def test_compute_drift_no_targets_table_is_empty(tmp_path):
    conn = _seed_db()
    assert gen.compute_drift(conn, tmp_path / "absent.md") == []


def test_validate_recs_drops_runaway_query(monkeypatch):
    # A model-authored evidence_sql that runs away must be interrupted and dropped,
    # never hang the run. Keep the watchdog short so the test is fast.
    monkeypatch.setattr(gen, "EVIDENCE_SQL_TIMEOUT_S", 1)
    conn = _seed_db()
    slow = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL "
            "SELECT x+1 FROM c WHERE x<100000000) SELECT count(*) FROM c")
    out = gen.validate_recs(
        [{"title": "runaway", "evidence_sql": slow},
         {"title": "ok", "evidence_sql": "SELECT 1"}],
        conn,
    )
    assert [r["title"] for r in out] == ["ok"]


# ---------------------------------------------------------------- privacy / safety
def test_no_query_touches_input_preview():
    for sql in gen.ALLOWED_QUERIES.values():
        assert "input_preview" not in sql
    for sql in gen.RULE_CHECKS.values():
        assert "input_preview" not in sql


def test_claude_path_is_a_binary_not_shell_function():
    # T4: subprocess must hit the real binary, never the ~/.bashrc claude() function.
    p = gen.CLAUDE_PATH
    assert "function" not in p
    assert p == "claude" or Path(p).name == "claude"
