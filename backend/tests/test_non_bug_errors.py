"""Harness refusals must not count as tool failures.

is_error=1 covers two very different things: a tool that broke, and a tool that
correctly declined a request it could not serve. Counting the second kind buried
the first — the top two 7-day fingerprints were a subagent capacity message
(41 hits) and a fork-nesting refusal (14), sitting above the only genuine
repeat, WebFetch against web.archive.org at 11.

The exclusion existed in exactly one of the three places that needed it, and
even there carried two of the five prefixes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.api.panels import _NON_BUG_ERROR_PREFIXES, _non_bug_error_clause

_SCHEMA = Path(__file__).resolve().parent.parent / "app" / "schema.sql"

REAL_ERRORS = [
    "Claude Code is unable to fetch from web.archive.org",
    # `git push` prints this for a non-fast-forward push. An unanchored
    # '%rejected%' match caught 36 rows like it; that is why every prefix is
    # anchored at the start of the string.
    "Exit code 1\n ! [rejected]        main -> main (fetch first)",
    "<tool_use_error>String to replace not found in file.",
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA.read_text())
    return conn


def _insert(conn, preview, i):
    conn.execute(
        "INSERT INTO tool_calls (tool_use_id, session_id, tool_name, is_error,"
        " error_preview, started_at) VALUES (?,?,?,?,?,?)",
        (f"t{i}", "s1", "Bash", 1, preview, "2026-08-26T12:00:00Z"),
    )


def _surviving(previews: list[str | None]) -> list[str | None]:
    conn = _conn()
    for i, p in enumerate(previews):
        _insert(conn, p, i)
    rows = conn.execute(
        f"SELECT error_preview FROM tool_calls WHERE {_non_bug_error_clause()}"
    ).fetchall()
    return [r[0] for r in rows]


def test_clause_is_valid_sql_for_every_prefix():
    """The literal for "You've hit your session limit" contains an apostrophe.
    Un-doubled, it terminates the SQL string early and the whole query dies."""
    conn = _conn()
    conn.execute(f"SELECT COUNT(*) FROM tool_calls WHERE {_non_bug_error_clause()}")


def test_every_declared_prefix_is_actually_excluded():
    samples = [p.rstrip("%") + " ...trailing detail" for p in _NON_BUG_ERROR_PREFIXES]
    assert _surviving(samples) == []


def test_real_tool_errors_survive():
    assert _surviving(REAL_ERRORS) == REAL_ERRORS


def test_null_previews_are_kept():
    """A NULL preview carries no evidence either way. Dropping those would
    silently shrink the denominator of every error-rate figure."""
    assert _surviving([None]) == [None]


def test_prefixes_are_anchored_not_substrings():
    """A refusal phrase quoted inside a genuine error must still count."""
    quoted = "Exit code 1\nbash: warning: Concurrent subagent limit reached"
    assert _surviving([quoted]) == [quoted]


def test_alternate_column_name_is_respected():
    assert _non_bug_error_clause("e").startswith("(e IS NULL")
