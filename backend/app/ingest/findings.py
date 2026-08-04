"""Producer mapping for the unified `findings` table.

Three systems produced actionable observations, none of which could be acted
on, and each of which had its own shape:

  detector  waste_findings rows      pattern/severity/detail/tokens_saved/fix_*
  signal    api.panels.intelligence  id/title/body/action/severity  (live SQL)
  drift     scripts/generate_...     title/body/evidence_sql/tier/rule_ref
  llm       same file, Haiku call    title/body/evidence_sql/confidence/tier

This module owns the translation into one row shape and the scan lifecycle.
Panels read `findings`; nothing reads the three native shapes any more.

Lifecycle: a scan UPSERTs every finding it sees under a fresh scan id, then
marks anything it did not see as resolved. Nothing is ever deleted. The old
waste_findings table was rewritten wholesale every 30 minutes, which is why it
could carry no state, no history and no verification — and why a delete
predicate that pruned nothing let it grow to 1169 rows for 7 real findings.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------- mapping --

# pattern -> (gauge, fix_kind, auto_run)
#
# auto_run is the short leash, and it is deliberately two entries long. Both
# are single reversible settings changes: a line in a shell profile, and an MCP
# server the operator can re-add. Everything that edits a file inside a project
# hands the operator a prompt instead, because reviewing that diff is the work
# — a button would only move where it happens, not remove it.
#
# ghost_agents and ghost_skills are `shell` but NOT auto_run: archiving 835
# directories unattended is a wider reach than was agreed, and the fix for
# plugin-supplied skills is scoping rather than moving them anyway.
DETECTOR_MAP: dict[str, tuple[str, str, int]] = {
    "junk_reads":          ("hygiene", "prompt", 0),
    "duplicate_reads":     ("hygiene", "prompt", 0),
    "low_read_edit_ratio": ("hygiene", "prompt", 0),
    "bloated_claude_md":   ("hygiene", "prompt", 0),
    # unused_mcp is shell but NOT auto_run. Removing an MCP server cannot be
    # undone by this dashboard: the server's original scope (user / .mcp.json
    # project / local) and its transport, args and env are not recorded
    # anywhere the scan can read, so there is no faithful `claude mcp add` to
    # hand back. It shipped as auto_run=1 alongside a handler that returned
    # undo_command=None, which is exactly the contradiction the short-leash +
    # undo-log choice rules out. Demonstrated live on 2026-08-04: the removal
    # failed against a .mcp.json-scoped server and the attempted restore landed
    # in a different scope than the original.
    "unused_mcp":          ("hygiene", "shell",  0),
    "bash_bloat":          ("hygiene", "shell",  1),
    "ghost_agents":        ("hygiene", "shell",  0),
    "ghost_skills":        ("hygiene", "shell",  0),
}

# intelligence() emits ids, some of them templated per tool/model family.
# Prefix match, longest first, so "flaky-tool-Bash" resolves via "flaky-tool-".
SIGNAL_GAUGE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("error-rate-elevated", "hygiene"),
    ("flaky-tool-",         "hygiene"),
    ("agent-timeouts",      "hygiene"),
    ("context-rot",         "efficiency"),
    ("cache-low",           "efficiency"),
    ("-over-target",        "discipline"),   # suffix; see signal_gauge()
    ("-on-target",          "discipline"),
)

# CLAUDE.md drift rule id -> gauge. Ids come from the Measurable Targets table.
DRIFT_GAUGE: dict[str, str] = {
    "fable_cost_share": "discipline",
    "opus_cost_share":  "discipline",
    "cache_hit":        "efficiency",
    "fail_rate":        "hygiene",
    "repeat_err":       "hygiene",
    "web_err":          "hygiene",
}


def signal_gauge(signal_id: str) -> str:
    """Gauge for an intelligence() rule id. Unknown ids land on hygiene."""
    for token, gauge in SIGNAL_GAUGE_PREFIXES:
        if signal_id.startswith(token) or signal_id.endswith(token):
            return gauge
    return "hygiene"


def llm_key(title: str) -> str:
    """Key for a model-authored recommendation.

    Hashed from the title because there is no stable id to hand: Haiku rewords
    the same observation between runs, so an accept/snooze on one of these is
    best-effort and will not survive a rephrase. Detector, signal and drift
    findings all carry real ids and do not have this problem.
    """
    return "llm:" + hashlib.sha1(title.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- lifecycle --

_UPSERT = """
INSERT INTO findings (key, source, gauge, severity, title, detail, est_tokens,
                      est_usd, evidence_sql, fix_kind, fix_label, fix_text,
                      auto_run, first_seen, last_seen, last_scan, resolved_at)
VALUES (:key, :source, :gauge, :severity, :title, :detail, :est_tokens,
        :est_usd, :evidence_sql, :fix_kind, :fix_label, :fix_text,
        :auto_run, :now, :now, :scan_id, NULL)
ON CONFLICT(key) DO UPDATE SET
  severity     = excluded.severity,
  title        = excluded.title,
  detail       = excluded.detail,
  est_tokens   = excluded.est_tokens,
  est_usd      = excluded.est_usd,
  evidence_sql = excluded.evidence_sql,
  fix_kind     = excluded.fix_kind,
  fix_label    = excluded.fix_label,
  fix_text     = excluded.fix_text,
  auto_run     = excluded.auto_run,
  last_seen    = excluded.last_seen,
  last_scan    = excluded.last_scan,
  resolved_at  = NULL
"""

_FIELDS = ("key", "source", "gauge", "severity", "title", "detail", "est_tokens",
           "est_usd", "evidence_sql", "fix_kind", "fix_label", "fix_text", "auto_run")


def new_scan_id() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def upsert(conn: sqlite3.Connection, scan_id: str, rows: list[dict]) -> int:
    """Write findings for this scan. first_seen survives; resolved_at clears."""
    now = new_scan_id()
    for row in rows:
        params = {f: row.get(f) for f in _FIELDS}
        params.update(now=now, scan_id=scan_id)
        conn.execute(_UPSERT, params)
    return len(rows)


def resolve_missing(conn: sqlite3.Connection, scan_id: str, sources: list[str]) -> int:
    """Mark findings this scan stopped seeing as resolved.

    Scoped to the sources the scan actually ran: a detector pass must not
    resolve the weekly llm rows just because it did not produce them.
    """
    placeholders = ",".join("?" * len(sources))
    cur = conn.execute(
        f"""UPDATE findings SET resolved_at = ?
             WHERE resolved_at IS NULL AND last_scan != ? AND source IN ({placeholders})""",
        (new_scan_id(), scan_id, *sources),
    )
    return cur.rowcount


# Everything the fix queue shows: still present, not a positive confirmation,
# and not a decision the operator has already made.
_OPEN_WHERE = """
    WHERE f.resolved_at IS NULL
      AND f.severity != 'ok'
      AND (s.status IS NULL OR s.status = 'open'
           OR (s.status = 'snoozed' AND s.snooze_until IS NOT NULL
               AND s.snooze_until <= :now))
"""
# Cheapest measured cost first, with unmeasurable last rather than first —
# a NULL est_tokens means "we cannot price this", and sorting those to the top
# would put the least-substantiated findings in front of the best-evidenced.
_RANK = """
 ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
          f.est_tokens IS NULL, f.est_tokens DESC
"""

OPEN_FINDINGS_SQL = f"""
SELECT f.* FROM findings f
     LEFT JOIN finding_state s ON s.key = f.key
{_OPEN_WHERE}{_RANK}
"""

# What the hygiene grade counts. Same set minus llm rows: a model-authored
# finding has no stable key (see llm_key), so Haiku rephrasing one observation
# between weekly runs would look like a finding resolving and a new one
# appearing, and the grade would move without anything changing on the
# machine. The grade has to be reproducible from the machine's state alone.
GRADED_FINDINGS_SQL = f"""
SELECT f.* FROM findings f
     LEFT JOIN finding_state s ON s.key = f.key
{_OPEN_WHERE}  AND f.source != 'llm'
{_RANK}
"""


if __name__ == "__main__":  # python -m app.ingest.findings
    import pathlib

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        (pathlib.Path(__file__).resolve().parent.parent / "schema.sql").read_text()
    )
    conn.row_factory = sqlite3.Row

    def row(key, **kw):
        base = dict(key=key, source="detector", gauge="hygiene", severity="high",
                    title=key, detail=None, est_tokens=None, est_usd=None,
                    evidence_sql=None, fix_kind="prompt", fix_label=None,
                    fix_text=None, auto_run=0)
        base.update(kw)
        return base

    s1 = "scan-1"
    upsert(conn, s1, [row("detector:a"), row("detector:b")])
    first_a = conn.execute("SELECT first_seen FROM findings WHERE key='detector:a'").fetchone()[0]

    # Second scan drops 'b'. 'a' keeps its first_seen; 'b' resolves, not deleted.
    s2 = "scan-2"
    upsert(conn, s2, [row("detector:a")])
    assert resolve_missing(conn, s2, ["detector"]) == 1
    assert conn.execute("SELECT first_seen FROM findings WHERE key='detector:a'").fetchone()[0] == first_a
    assert conn.execute("SELECT resolved_at FROM findings WHERE key='detector:b'").fetchone()[0] is not None
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 2, "must not delete"

    # 'b' comes back — reopened, original first_seen intact.
    upsert(conn, "scan-3", [row("detector:a"), row("detector:b")])
    assert conn.execute("SELECT resolved_at FROM findings WHERE key='detector:b'").fetchone()[0] is None

    # A detector scan must not resolve the weekly llm rows.
    upsert(conn, "llm-scan", [row(llm_key("x"), source="llm", fix_kind="manual", title="x")])
    upsert(conn, "scan-4", [row("detector:a"), row("detector:b")])
    assert resolve_missing(conn, "scan-4", ["detector"]) == 0
    assert conn.execute("SELECT resolved_at FROM findings WHERE source='llm'").fetchone()[0] is None

    # Grade excludes accepted, ok, and un-elapsed snoozes.
    upsert(conn, "scan-5", [row("detector:a"), row("detector:b"),
                            row("signal:fine", source="signal", severity="ok")])
    now = "2026-08-04T00:00:00Z"
    conn.execute("INSERT INTO finding_state(key,status,changed_at) VALUES('detector:a','accepted',?)", (now,))
    conn.execute("INSERT INTO finding_state(key,status,snooze_until,changed_at) VALUES('detector:b','snoozed','2099-01-01T00:00:00Z',?)", (now,))
    # Queue still shows the llm row; grade must not, or a Haiku rephrase would
    # move the score with nothing having changed on the machine.
    queue = [r["key"] for r in conn.execute(OPEN_FINDINGS_SQL, {"now": now})]
    graded = [r["key"] for r in conn.execute(GRADED_FINDINGS_SQL, {"now": now})]
    assert queue == [llm_key("x")], f"accepted/snoozed/ok must drop out: {queue}"
    assert graded == [], f"llm must not be graded: {graded}"

    assert signal_gauge("opus-over-target") == "discipline"
    assert signal_gauge("flaky-tool-Bash") == "hygiene"
    assert signal_gauge("cache-low") == "efficiency"
    assert signal_gauge("something-new") == "hygiene"
    # One auto-run fix, and it must be the reversible one. api/fixactions.py
    # asserts at import that every auto_run pattern here has a handler
    # declaring a working undo, so widening this set fails loudly at startup
    # rather than at the moment someone presses the button.
    auto = [p for p, (_, _, a) in DETECTOR_MAP.items() if a]
    assert auto == ["bash_bloat"], f"short leash drifted: {auto}"

    print("findings mapping self-check: 14/14")
