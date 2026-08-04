#!/usr/bin/env python3
"""Claude Code Dashboard — weekly suggestion generator (v3 pass_1).

Reads aggregate SQL queries from dashboard.db (allowlist of 6; no input_preview
access), asks claude-haiku via CLI for up to 5 forensic recommendations, writes
atomically to data/recommendations.json. Appends one-line JSON audit entry to
data/generator.log per run.

PRIVACY: input_preview column is structurally excluded from all queries. Live DB
inspection on 2026-04-23 confirmed 22 sk-ant-api03-* API key fragments in that
field. Zero such hits in error_preview. error_preview is truncated to 80 chars
in all queries.

Usage:
    python scripts/generate_recommendations.py            # live run
    python scripts/generate_recommendations.py --dry-run  # skip claude, print SQL results

Runs manually or via systemd user timer (Sunday 09:00, opt-in). Exits 0 on
success, 1 on any error (network, API, parse, validation). Never crashes with
an uncaught exception — errors go to stderr + generator.log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# ---------------------------------------------------------------- paths --
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CLAUDE_DASHBOARD_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "dashboard.db"
OUTPUT_FILE = DATA_DIR / "recommendations.json"
LOG_FILE = DATA_DIR / "generator.log"

sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.ingest import findings as findings_db  # noqa: E402

# ---------------------------------------------------------------- CLI ----
# CLAUDE_BIN lets headless runners (systemd) point at a concrete binary when the
# `claude` shim can't resolve a version without an active shell (e.g. mise shims).
CLAUDE_PATH = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
TIMEOUT_S = 150  # cold headless `claude -p` (cache creation + multi-turn) can exceed 120s; stay under the service's 180s hard limit
EVIDENCE_SQL_TIMEOUT_S = 5  # per model-authored evidence_sql; a runaway query must not hang the run
MAX_BUDGET_USD = 0.15
MODEL = "haiku"
FALLBACK_MODEL = "sonnet"

# ---------------------------------------------------------------- drift --
# CLAUDE.md file holding the "Measurable Targets (Drift-Checked)" table.
# Drift is computed locally (Python + read-only SQL); NOTHING here is ever
# sent to a model. The model call (telemetry observations) is unchanged.
CLAUDE_MD_FILE = Path(
    os.environ.get("CLAUDE_DRIFT_RULES_FILE", Path.home() / "CLAUDE.md")
)

# ---------------------------------------------------------------- tags --
# Mirrors backend.config.PROJECT_TAG_RULES. First-match wins.
# Populate with your own project paths, e.g.:
#   (str(Path.home() / "projects" / "myapp"), "myapp"),
PROJECT_TAG_RULES: list[tuple[str, str]] = []

# ---------------------------------------------------------------- queries
# AGGREGATES ONLY. NO input_preview. error_preview capped at 80 chars.
ALLOWED_QUERIES: dict[str, str] = {
    "error_fingerprints": """
        SELECT tool_name,
               substr(error_preview, 1, 80) AS fp,
               COUNT(*) AS hits
          FROM tool_calls
         WHERE started_at >= date('now', '-30 days')
           AND is_error = 1
           AND error_preview IS NOT NULL
      GROUP BY tool_name, fp
        HAVING COUNT(*) >= 2
      ORDER BY hits DESC
         LIMIT 10
    """,
    "token_mix_by_model": """
        SELECT model,
               COUNT(*) AS messages,
               COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_create_tokens), 0) AS tokens
          FROM messages
         WHERE ts >= datetime('now', '-30 days')
           AND role = 'assistant'
           AND model IS NOT NULL
      GROUP BY model
      ORDER BY tokens DESC
         LIMIT 10
    """,
    "project_tokens_7d": """
        SELECT s.cwd AS cwd,
               COUNT(DISTINCT s.id) AS sessions,
               COALESCE(SUM(m.input_tokens + m.output_tokens + m.cache_read_tokens + m.cache_create_tokens), 0) AS tokens
          FROM sessions s
          LEFT JOIN messages m ON m.session_id = s.id
         WHERE s.started_at >= datetime('now', '-7 days')
      GROUP BY s.cwd
      ORDER BY tokens DESC
         LIMIT 10
    """,
    "skill_invocations_30d": """
        SELECT skill_name,
               COUNT(*) AS invocations,
               COUNT(DISTINCT session_id) AS sessions
          FROM skill_invocations
         WHERE ts >= datetime('now', '-30 days')
      GROUP BY skill_name
      ORDER BY invocations DESC
         LIMIT 15
    """,
    "session_outcomes_30d": """
        SELECT substr(started_at, 1, 10) AS day,
               outcome,
               COUNT(*) AS n
          FROM sessions
         WHERE started_at >= date('now', '-30 days')
      GROUP BY day, outcome
      ORDER BY day DESC, n DESC
         LIMIT 30
    """,
    "status_snapshot": """
        SELECT (SELECT COUNT(*) FROM sessions WHERE last_modified_ts >= strftime('%s','now') - 300) AS live_sessions,
               (SELECT COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_create_tokens), 0)
                  FROM messages WHERE substr(ts,1,10) = date('now','utc')) AS tokens_today,
               (SELECT COUNT(*) FROM tool_calls WHERE is_error = 1 AND started_at >= datetime('now','-7 days')) AS errors_7d
    """,
}

# ---------------------------------------------------------------- schema --
JSON_SCHEMA = {
    "type": "object",
    "required": ["recommendations", "notes"],
    "properties": {
        "recommendations": {
            "type": "array",
            "minItems": 0,
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["title", "body", "evidence_sql", "confidence", "tier"],
                "properties": {
                    "title": {"type": "string", "maxLength": 80},
                    "body": {"type": "string", "maxLength": 500},
                    "evidence_sql": {"type": "string", "maxLength": 300},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "tier": {"type": "string", "enum": ["high", "medium", "low"]},
                    # Optional drift fields (set by the deterministic drift engine,
                    # null on model-generated recs). Declared so the shape is valid.
                    "rule_ref": {"type": ["string", "null"], "maxLength": 120},
                    "expected": {"type": ["string", "null"], "maxLength": 300},
                    "actual": {"type": ["string", "null"], "maxLength": 120},
                    "drift_score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "notes": {"type": ["string", "null"], "maxLength": 200},
    },
    "additionalProperties": False,
}

# ---------------------------------------------------------------- filter -
BANNED_PHRASE_RE = re.compile(
    r"""(?xi)
    \b you \s+ must \b
    | \b you \s+ need \s+ to \b
    | \b immediately \b
    | \b (?:this \s+ is \s+)? critical \b
    | \b urgent (?:ly)? \b
    | \b you(?:'re|\s+are) \s+ doing \s+ it \s+ wrong \b
    | \b you \s+ should \s+ stop \b
    | \b bad \s+ practice \b
    | \b (?:is \s+)? harmful \b
    | \b dangerous \b
    | \b too \s+ much \b
    | \b using \s+ too \b
    """,
    re.IGNORECASE,
)

# ---------------------------------------------------------------- prompt -
PROMPT_TEMPLATE = """You are an analytics assistant for a personal Claude Code usage dashboard. Your job is to generate 0 to 5 evidence-based observations about the user's Claude Code usage patterns.

DATABASE SCHEMA (write evidence_sql against these real tables — NOT the section headers below):

  sessions(id, project_hash, cwd, model, started_at, last_event_at, last_user_message,
           last_modified_ts, entrypoint, outcome, total_tool_errors, event_count)
  messages(uuid, session_id, parent_uuid, role, model, ts,
           input_tokens, output_tokens, cache_read_tokens, cache_create_tokens)
  tool_calls(tool_use_id, session_id, tool_name, is_mcp, mcp_server, mcp_tool,
             started_at, ended_at, duration_ms, is_error, error_preview)
  skill_invocations(id, session_id, skill_name, ts)

RULES:
- Each recommendation must be anchored to at least one specific number from the query results.
- Write in observation style: "Your X data shows Y" not "You should stop using X."
- Do NOT use: "you must", "you need to", "immediately", "critical", "urgent", "you should stop", "bad practice", "harmful", "dangerous", "too much".
- Confidence 0.5-0.7 for ambiguous observations, 0.8-0.95 for clear patterns.
- Tier "high" affects cost or workflow meaningfully. "medium" is notable. "low" is a curiosity.
- Keep body to 2-4 sentences. Cite specific numbers.
- evidence_sql: valid SQLite SELECT that reproduces the key number. Use the tables above. Short (no CTEs unless essential).
- Silence is valid. Return 0 recommendations if nothing clears the bar.

QUERY RESULTS (section headers are NOT tables — they label the aggregate; run evidence_sql against sessions / messages / tool_calls / skill_invocations):

{QUERY_RESULTS}

Respond with a JSON object matching the provided schema exactly. No prose outside the JSON."""


# ---------------------------------------------------------------- drift --
# Each check returns a single column `actual` (a number) or NULL (→ skip,
# no data in window). Thresholds come from the CLAUDE.md table, not here —
# this file stays authoritative for the numbers. The `id` keys link them.
RULE_CHECKS: dict[str, str] = {
    # Fable and Opus get separate checks, each on spend rather than tokens.
    # They used to share one "premium_share" threshold, but Fable (~5x
    # Sonnet's price) and Opus (~2.5x, after its own price cut) warrant
    # different ceilings — one combined check either let the cheaper model
    # run wild or capped it at the pricier one's limit.
    "fable_cost_share": """
        SELECT ROUND(100.0 *
          COALESCE(SUM(CASE WHEN model LIKE '%fable%' THEN cost_usd END), 0)
          / NULLIF(SUM(cost_usd), 0), 1) AS actual
          FROM messages
         WHERE role='assistant' AND ts >= datetime('now','-30 days')
    """,
    "opus_cost_share": """
        SELECT ROUND(100.0 *
          COALESCE(SUM(CASE WHEN model LIKE '%opus%' THEN cost_usd END), 0)
          / NULLIF(SUM(cost_usd), 0), 1) AS actual
          FROM messages
         WHERE role='assistant' AND ts >= datetime('now','-30 days')
    """,
    "cache_hit": """
        SELECT ROUND(100.0 * COALESCE(SUM(cache_read_tokens),0)
          / NULLIF(SUM(cache_read_tokens + input_tokens),0), 1) AS actual
          FROM messages
         WHERE role='assistant' AND ts >= datetime('now','-30 days')
    """,
    "fail_rate": """
        SELECT ROUND(100.0 * SUM(CASE WHEN outcome IN ('errored','rate_limited','truncated')
                                      THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*),0), 1) AS actual
          FROM sessions
         WHERE started_at >= datetime('now','-30 days')
    """,
    "repeat_err": """
        SELECT COALESCE(MAX(hits),0) AS actual FROM (
          SELECT COUNT(*) AS hits FROM tool_calls
           WHERE is_error=1 AND error_preview IS NOT NULL
             AND started_at >= datetime('now','-7 days')
        GROUP BY tool_name, substr(error_preview,1,80)
        )
    """,
    "web_err": """
        SELECT ROUND(100.0 * SUM(CASE WHEN is_error=1 THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*),0), 1) AS actual
          FROM tool_calls
         WHERE tool_name='WebFetch' AND started_at >= datetime('now','-30 days')
    """,
}

_THRESHOLD_RE = re.compile(r"(<=|>=|<|>|=)\s*([0-9]+(?:\.[0-9]+)?)\s*(%?)")


def parse_drift_targets(claude_md: Path) -> dict[str, dict]:
    """Parse the 'Measurable Targets (Drift-Checked)' pipe table from CLAUDE.md.
    Returns {id: {label, window, op, threshold, unit, raw}}. Empty on any failure
    — a missing/garbled table simply yields zero drift checks, never a crash.
    """
    try:
        text = claude_md.read_text(errors="replace")
    except OSError:
        return {}
    out: dict[str, dict] = {}
    in_section = False
    for line in text.splitlines():
        if line.lstrip().startswith("## "):
            in_section = "Measurable Targets" in line
            continue
        if not in_section or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        rid = cells[0]
        if rid in ("id", "") or set(rid) <= {"-", ":"}:
            continue  # header or separator row
        m = _THRESHOLD_RE.search(cells[3])
        if not m:
            continue
        out[rid] = {
            "label": cells[1],
            "window": cells[2],
            "op": m.group(1),
            "threshold": float(m.group(2)),
            "unit": m.group(3),
            "raw": cells[3],
        }
    return out


def _satisfies(actual: float, op: str, threshold: float) -> bool:
    return {
        "<": actual < threshold,
        "<=": actual <= threshold,
        ">": actual > threshold,
        ">=": actual >= threshold,
        "=": actual == threshold,
    }.get(op, True)


def compute_drift(conn: sqlite3.Connection, claude_md: Path) -> list[dict]:
    """Deterministic intent-vs-behavior check: CLAUDE.md targets vs telemetry.
    Pure local computation — nothing is sent to any model. Returns rec dicts
    (same shape as model recs, plus rule_ref/expected/actual/drift_score).
    A check runs only if its id is BOTH in RULE_CHECKS and declared in CLAUDE.md.
    """
    targets = parse_drift_targets(claude_md)
    recs: list[dict] = []
    for rid, sql in RULE_CHECKS.items():
        tgt = targets.get(rid)
        if not tgt:
            continue  # not declared in CLAUDE.md → skip
        try:
            row = conn.execute(sql).fetchone()
        except sqlite3.Error as e:
            print(f"[DRIFT] check {rid} SQL error: {e}", file=sys.stderr)
            continue
        actual = row["actual"] if row else None
        if actual is None:
            continue  # no data in window → no drift claim
        actual = float(actual)
        if _satisfies(actual, tgt["op"], tgt["threshold"]):
            continue  # compliant → no drift
        gap = abs(actual - tgt["threshold"])
        drift_score = round(max(0.0, min(1.0, gap / max(tgt["threshold"], 1.0))), 2)
        actual_str = f"{actual:g}{tgt['unit']}"
        recs.append({
            "title": f"Drift: {tgt['label']} is {actual_str} (target {tgt['raw']})"[:80],
            "body": (
                f"CLAUDE.md target '{tgt['label']}' over {tgt['window']} is {tgt['raw']}; "
                f"telemetry measures {actual_str}. Deterministic local check, no model involved."
            )[:500],
            "evidence_sql": " ".join(sql.split()),  # full SQL — must stay runnable
            "confidence": 1.0,
            "tier": "high" if drift_score >= 0.5 else "medium",
            "rule_ref": f"CLAUDE.md:{rid}",
            "expected": f"{tgt['label']} {tgt['raw']}"[:300],
            "actual": actual_str[:120],
            "drift_score": drift_score,
        })
    recs.sort(key=lambda r: r["drift_score"], reverse=True)
    return recs


# ---------------------------------------------------------------- code ---
def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _tag_for_cwd(cwd: str | None) -> str:
    if not cwd:
        return "untagged"
    for prefix, tag in PROJECT_TAG_RULES:
        if cwd.startswith(prefix):
            return tag
    return "untagged"


def connect_readonly() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def run_queries(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name, sql in ALLOWED_QUERIES.items():
        rows = conn.execute(sql).fetchall()
        out[name] = [dict(r) for r in rows]
    # Post-process project_tokens: replace raw cwd with tag, re-aggregate
    project = out.get("project_tokens_7d", [])
    rolled: dict[str, dict[str, int]] = {}
    for r in project:
        tag = _tag_for_cwd(r.get("cwd"))
        bucket = rolled.setdefault(tag, {"sessions": 0, "tokens": 0})
        bucket["sessions"] += int(r.get("sessions") or 0)
        bucket["tokens"] += int(r.get("tokens") or 0)
    out["project_tokens_7d"] = [
        {"tag": tag, **data}
        for tag, data in sorted(rolled.items(), key=lambda kv: kv[1]["tokens"], reverse=True)
    ]
    return out


def build_prompt(results: dict[str, list[dict]]) -> str:
    sections: list[str] = []
    for name, rows in results.items():
        sections.append(f"=== {name.upper()} ===")
        if not rows:
            sections.append("(no data)")
            continue
        cols = list(rows[0].keys())
        sections.append(" | ".join(cols))
        for row in rows:
            sections.append(" | ".join(str(row.get(c, "")) for c in cols))
        sections.append("")
    body = "\n".join(sections)
    return PROMPT_TEMPLATE.format(QUERY_RESULTS=body)


def call_claude(prompt: str) -> dict:
    """Invoke claude CLI. Returns the structured_output field as a dict.
    Raises RuntimeError on any failure.
    """
    schema_str = json.dumps(JSON_SCHEMA)
    cmd = [
        CLAUDE_PATH, "-p", prompt,
        "--model", MODEL,
        "--fallback-model", FALLBACK_MODEL,
        "--output-format", "json",
        "--json-schema", schema_str,
        "--no-session-persistence",
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", str(MAX_BUDGET_USD),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude CLI timeout after {TIMEOUT_S}s") from e
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: stderr={proc.stderr[:500]}"
        )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude CLI output not JSON: {proc.stdout[:500]}") from e
    if envelope.get("is_error"):
        raise RuntimeError(
            f"claude CLI is_error=true subtype={envelope.get('subtype')} "
            f"errors={envelope.get('errors')}"
        )
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        raise RuntimeError(
            f"claude CLI returned no structured_output field; keys={list(envelope.keys())[:20]}"
        )
    return {
        "structured": structured,
        "model": envelope.get("modelUsage", {}),
        "cost_usd": envelope.get("total_cost_usd"),
        "duration_ms": envelope.get("duration_ms"),
    }


def warn_filter(recommendations: list[dict]) -> int:
    """WARN-ONLY: count recs containing banned phrases. Never removes them."""
    count = sum(
        1 for rec in recommendations
        if BANNED_PHRASE_RE.search(
            (rec.get("title") or "") + " " + (rec.get("body") or "")
        )
    )
    print(f"[SUGGESTIONS] filter_would_reject_count={count}", file=sys.stderr)
    return count


def validate_recs(recs: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """Drop recs whose evidence_sql fails to execute (SELECT only, dry-check)."""
    out: list[dict] = []
    for rec in recs:
        sql = rec.get("evidence_sql", "").strip()
        if not sql:
            print(f"[SUGGESTIONS] dropped rec (empty evidence_sql): {rec.get('title')}", file=sys.stderr)
            continue
        # Permit only SELECT / WITH
        leading = sql.lstrip().split()[0].upper() if sql.lstrip() else ""
        if leading not in ("SELECT", "WITH"):
            print(f"[SUGGESTIONS] dropped rec (non-SELECT evidence_sql): {rec.get('title')}", file=sys.stderr)
            continue
        # Model-authored SQL — guard against a runaway query hanging the whole
        # run. A timer fires conn.interrupt(), which raises sqlite3.OperationalError
        # (a sqlite3.Error) and drops the offending rec.
        watchdog = threading.Timer(EVIDENCE_SQL_TIMEOUT_S, conn.interrupt)
        watchdog.start()
        try:
            conn.execute(sql).fetchmany(1)
        except sqlite3.Error as e:
            print(f"[SUGGESTIONS] dropped rec (SQL error: {e}): {rec.get('title')}", file=sys.stderr)
            continue
        finally:
            watchdog.cancel()
        out.append(rec)
    return out


def write_findings(drift_recs: list[dict], llm_recs: list[dict]) -> None:
    """Upsert this run's recs into the unified findings table.

    Deliberately a SEPARATE writable connection from the `conn` used for
    query_results/validate_recs — that one is opened read-only as a privacy
    guard (see module docstring) and must stay that way regardless of what
    else this script starts writing.

    llm_recs never get fix_kind='shell' or auto_run=1: the findings table's
    CHECK constraint already forbids source='llm' from auto-running, and this
    sets it explicitly so the intent reads at the call site, not just in a
    schema file three commits away.
    """
    rows = []
    for r in drift_recs:
        rid = r["rule_ref"].split(":", 1)[-1]
        rows.append({
            "key":          f"drift:{rid}",
            "source":       "drift",
            "gauge":        findings_db.DRIFT_GAUGE.get(rid, "hygiene"),
            "severity":     r["tier"],
            "title":        r["title"],
            "detail":       r["body"],
            "est_tokens":   None,
            "est_usd":      None,
            "evidence_sql": r["evidence_sql"],
            "fix_kind":     "manual",
            "fix_label":    None,
            "fix_text":     None,
            "auto_run":     0,
        })
    for r in llm_recs:
        rows.append({
            "key":          findings_db.llm_key(r["title"]),
            "source":       "llm",
            "gauge":        "hygiene",  # no reliable per-rec classifier yet
            "severity":     r.get("tier", "low"),
            "title":        r["title"],
            "detail":       r.get("body"),
            "est_tokens":   None,
            "est_usd":      None,
            "evidence_sql": r.get("evidence_sql"),
            "fix_kind":     "manual",
            "fix_label":    None,
            "fix_text":     None,
            "auto_run":     0,
        })

    write_conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        scan_id = findings_db.new_scan_id()
        findings_db.upsert(write_conn, scan_id, rows)
        findings_db.resolve_missing(write_conn, scan_id, ["drift", "llm"])
        write_conn.commit()
    finally:
        write_conn.close()


def atomic_write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=str(path.parent), prefix=".tmp-", suffix=".json", delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def append_log_line(entry: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------- main ---
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate suggestion box recommendations")
    parser.add_argument("--dry-run", action="store_true", help="Skip claude CLI call; print query results as JSON")
    args = parser.parse_args(argv)

    start = dt.datetime.now(dt.UTC)
    log_entry: dict = {
        "ts": _utc_now_iso(),
        "model": None,
        "duration_s": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "recs_generated": 0,
        "recs_after_filter": 0,
        "filter_would_reject_count": 0,
        "drift_findings": 0,
        "error": None,
    }

    try:
        conn = connect_readonly()
    except sqlite3.Error as e:
        log_entry["error"] = f"db-connect: {e}"
        append_log_line(log_entry, LOG_FILE)
        print(f"[SUGGESTIONS] ERROR db-connect: {e}", file=sys.stderr)
        return 1

    try:
        try:
            results = run_queries(conn)
        except sqlite3.Error as e:
            log_entry["error"] = f"query: {e}"
            append_log_line(log_entry, LOG_FILE)
            print(f"[SUGGESTIONS] ERROR query: {e}", file=sys.stderr)
            return 1

        # Drift is computed locally (no model) — safe to show in dry-run.
        drift_recs = compute_drift(conn, CLAUDE_MD_FILE)
        log_entry["drift_findings"] = len(drift_recs)

        if args.dry_run:
            print(json.dumps(results, indent=2, default=str))
            print("\n" + "=" * 60, file=sys.stderr)
            print(f"DRIFT FINDINGS ({len(drift_recs)}) — computed locally, nothing sent to any model:",
                  file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            for r in drift_recs:
                print(f"  [{r['drift_score']:.2f}] {r['rule_ref']}: "
                      f"expected {r['expected']!r} / actual {r['actual']!r}", file=sys.stderr)
            if not drift_recs:
                print("  (none — all declared CLAUDE.md targets are within threshold, "
                      "or no data in window)", file=sys.stderr)
            return 0

        prompt = build_prompt(results)

        try:
            cc = call_claude(prompt)
        except RuntimeError as e:
            log_entry["error"] = f"claude: {e}"
            append_log_line(log_entry, LOG_FILE)
            print(f"[SUGGESTIONS] ERROR claude: {e}", file=sys.stderr)
            return 1

        structured = cc["structured"]
        recs: list[dict] = structured.get("recommendations", []) or []
        notes: str | None = structured.get("notes")

        log_entry["recs_generated"] = len(recs)
        log_entry["filter_would_reject_count"] = warn_filter(recs)
        validated = validate_recs(recs, conn)
        log_entry["recs_after_filter"] = len(validated)

        model_info = cc.get("model") or {}
        model_name = next(iter(model_info.keys()), None)
        input_tokens = sum(int(v.get("inputTokens", 0) or 0) for v in model_info.values())
        output_tokens = sum(int(v.get("outputTokens", 0) or 0) for v in model_info.values())

        log_entry["model"] = model_name
        log_entry["input_tokens"] = input_tokens
        log_entry["output_tokens"] = output_tokens
        log_entry["cost_usd"] = cc.get("cost_usd")
        log_entry["duration_s"] = round((dt.datetime.now(dt.UTC) - start).total_seconds(), 2)

        # Drift recs lead (deterministic, high-signal), then model observations.
        all_recs = drift_recs + validated

        out = {
            "schema_version": 1,
            "generated_at": _utc_now_iso(),
            "generator_model": model_name or "claude-haiku",
            "notes": notes,
            "recommendations": all_recs,
        }
        atomic_write_json(out, OUTPUT_FILE)
        write_findings(drift_recs, validated)
        append_log_line(log_entry, LOG_FILE)
        print(f"[SUGGESTIONS] wrote {len(all_recs)} recs "
              f"({len(drift_recs)} drift + {len(validated)} model) to {OUTPUT_FILE}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
