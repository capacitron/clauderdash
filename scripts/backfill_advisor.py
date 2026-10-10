#!/usr/bin/env python3
"""Backfill advisor spend for transcript lines ingested before 3b07e16.

Replays only assistant events whose usage.iterations has an advisor_message,
through the live _handle_assistant. Idempotent: advisor rows are INSERT OR
IGNORE on a request_id-derived uuid, and the session cost bumps only on a new
row, so a second run adds nothing. Writes a VACUUM INTO backup first.

Usage (from repo root):  backend/.venv/bin/python scripts/backfill_advisor.py [--dry-run]
Restore: stop the server, cp the printed backup over data/dashboard.db.
"""
import json, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.ingest.jsonl import _handle_assistant, _read_agent_type, connect

dry = "--dry-run" in sys.argv
conn = connect()
if not dry:
    bak = Path(__file__).resolve().parent.parent / "data" / f"dashboard.db.bak-{time.strftime('%Y%m%d-%H%M%S')}-pre-advisor-backfill"
    conn.execute("VACUUM INTO ?", (str(bak),))
    print("backup:", bak)

def n_rows():
    r = conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd),0) FROM messages WHERE request_id LIKE '%#advisor'").fetchone()
    return r[0], r[1]

before = n_rows()
seen = set()
for f in sorted(Path.home().glob(".claude/projects/**/*.jsonl")):
    agent_type = _read_agent_type(f)
    try:
        lines = f.read_text(errors="ignore").splitlines()
    except OSError:
        continue
    for line in lines:
        if "advisor_message" not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        sid, rid = ev.get("sessionId"), ev.get("requestId")
        if ev.get("type") != "assistant" or not sid or (rid, ev.get("uuid")) in seen:
            continue
        seen.add((rid, ev.get("uuid")))
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            print("skip, session not in DB:", sid)
            continue
        if not dry:
            _handle_assistant(conn, ev, sid, agent_type)
if not dry:
    conn.commit()
after = n_rows()
print(f"advisor rows {before[0]} -> {after[0]}, cost ${before[1]:.2f} -> ${after[1]:.2f}")
