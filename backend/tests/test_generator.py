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
            cache_read_tokens INT, cache_create_tokens INT,
            cost_usd REAL
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
    # $8 total: opus $4 (50%), fable $2 (25%), haiku $2. Fable and Opus now
    # have separate targets (10% / 30%) instead of one combined one, so both
    # rows are needed to exercise fable_cost_share and opus_cost_share
    # independently — each breaches its own new threshold.
    conn.execute(
        f"INSERT INTO messages VALUES ('s1','assistant','claude-opus-4-8',{now},"
        "4000,0,0,0,4.0)"
    )
    conn.execute(
        f"INSERT INTO messages VALUES ('s1','assistant','claude-fable-5',{now},"
        "4000,0,0,0,2.0)"
    )
    conn.execute(
        f"INSERT INTO messages VALUES ('s1','assistant','claude-haiku-4-5',{now},"
        "2000,0,0,0,2.0)"
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
        "| fable_cost_share | Fable share | 30d | < 10% |\n"
        "| cache_hit | Cache hit | 30d | >= 70% |\n",
    )
    targets = gen.parse_drift_targets(f)
    assert set(targets) == {"fable_cost_share", "cache_hit"}
    assert targets["fable_cost_share"]["op"] == "<"
    assert targets["fable_cost_share"]["threshold"] == 10.0
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
        "| fable_cost_share | Fable | 30d | < 10% |\n"
    )
    targets = gen.parse_drift_targets(f)
    assert list(targets) == ["fable_cost_share"]


# ---------------------------------------------------------------- satisfies
@pytest.mark.parametrize("actual,op,thr,ok", [
    (15, "<", 20, True), (25, "<", 20, False),
    (70, ">=", 70, True), (69, ">=", 70, False),
    (2, "<", 3, True), (3, "<", 3, False),
])
def test_satisfies(actual, op, thr, ok):
    assert gen._satisfies(actual, op, thr) is ok


# ---------------------------------------------------------------- compute
def test_compute_drift_flags_fable_and_opus_independently(tmp_path):
    """Both rows declared → both breach, each against its OWN threshold —
    the whole point of splitting one combined check into two."""
    conn = _seed_db()
    f = _write_targets_table(
        tmp_path,
        "| fable_cost_share | Fable share | 30d | < 10% |\n"
        "| opus_cost_share | Opus share | 30d | < 30% |\n",
    )
    recs = {r["rule_ref"]: r for r in gen.compute_drift(conn, f)}
    assert set(recs) == {"CLAUDE.md:fable_cost_share", "CLAUDE.md:opus_cost_share"}

    fable = recs["CLAUDE.md:fable_cost_share"]
    assert fable["actual"] == "25%"       # $2 fable of $8 total
    assert fable["drift_score"] == 1.0
    assert fable["tier"] == "high"
    assert fable["evidence_sql"].lstrip().upper().startswith("SELECT")
    conn.execute(fable["evidence_sql"]).fetchone()

    opus = recs["CLAUDE.md:opus_cost_share"]
    assert opus["actual"] == "50%"        # $4 opus of $8 total
    # drift_score = clamp((actual - threshold) / threshold, 0, 1) = (50-30)/30
    assert opus["drift_score"] == pytest.approx(0.67, abs=0.01)
    assert opus["tier"] == "high"         # >= 0.5


def test_compute_drift_silent_when_compliant(tmp_path):
    conn = _seed_db()
    # Thresholds raised above the measured 25%/50% → compliant → no drift
    f = _write_targets_table(
        tmp_path,
        "| fable_cost_share | Fable share | 30d | < 90% |\n"
        "| opus_cost_share | Opus share | 30d | < 90% |\n",
    )
    assert gen.compute_drift(conn, f) == []


def test_compute_drift_null_actual_skipped(tmp_path):
    conn = _seed_db()  # no sessions rows → fail_rate ratio is NULL
    f = _write_targets_table(tmp_path, "| fail_rate | Fail rate | 30d | < 10% |\n")
    # NULL actual (no data in window) → skipped, never a false drift claim
    assert gen.compute_drift(conn, f) == []


def test_compute_drift_only_declared_rules_run(tmp_path):
    conn = _seed_db()  # fable/opus data present
    # Neither cost-share id declared → not checked even though data breaches both
    f = _write_targets_table(tmp_path, "| fail_rate | Fail rate | 30d | < 10% |\n")
    recs = gen.compute_drift(conn, f)
    assert all(
        r["rule_ref"] not in ("CLAUDE.md:fable_cost_share", "CLAUDE.md:opus_cost_share")
        for r in recs
    )


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
