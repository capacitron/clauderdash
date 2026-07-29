#!/usr/bin/env python3
"""v3 schema migration. Safe to run multiple times (idempotent).

Adds: messages.agent_type — which subagent (Explore, general-purpose,
compound-engineering:review:..., etc.) produced a given message row, read from
the `agent-<id>.meta.json` sidecar Claude Code writes next to each subagent
transcript.

The live ingester (app/ingest/jsonl.py) stamps agent_type on new rows as they
come in, but INSERT OR IGNORE never touches a uuid that's already in the
table — so every message ingested before this migration shipped is stuck at
NULL until backfilled here. This script does that backfill by walking every
`subagents/**/agent-*.meta.json` sidecar under CLAUDE_PROJECTS_DIR and
matching its sibling transcript's message uuids against the messages table.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("CLAUDE_DASHBOARD_DATA_DIR", _ROOT / "data"))
DB_PATH = _DATA_DIR / "dashboard.db"
CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects")
)


def _add_column(conn: sqlite3.Connection, table: str, col: str, definition: str) -> bool:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if col in existing:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    print(f"  + {table}.{col}")
    return True


def _iter_message_uuids(transcript: Path):
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                uuid = event.get("uuid")
                if uuid:
                    yield uuid
    except OSError:
        return


def run() -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH} — nothing to migrate.")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")

    print("=== v3 migration: agent_type ===")
    _add_column(conn, "messages", "agent_type", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_agent_type "
        "ON messages(agent_type) WHERE agent_type IS NOT NULL"
    )
    conn.commit()

    if not CLAUDE_PROJECTS_DIR.exists():
        print(f"Projects dir not found at {CLAUDE_PROJECTS_DIR} — skipping backfill.")
        return

    print(f"Backfilling agent_type from {CLAUDE_PROJECTS_DIR} …")
    sidecars = list(CLAUDE_PROJECTS_DIR.glob("**/subagents/**/*.meta.json"))
    updated_rows = 0
    matched_files = 0
    for meta_path in sidecars:
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        agent_type = meta.get("agentType")
        if not isinstance(agent_type, str):
            continue
        transcript = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        if not transcript.exists():
            continue
        uuids = list(_iter_message_uuids(transcript))
        if not uuids:
            continue
        matched_files += 1
        # SQLite caps variables per statement; chunk defensively for large
        # subagent transcripts.
        for i in range(0, len(uuids), 500):
            chunk = uuids[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"""
                UPDATE messages SET agent_type = ?
                 WHERE uuid IN ({placeholders}) AND agent_type IS NULL
                """,
                [agent_type, *chunk],
            )
            updated_rows += cur.rowcount

    conn.commit()
    print(f"  {matched_files}/{len(sidecars)} sidecars matched, {updated_rows} messages stamped")
    conn.close()


if __name__ == "__main__":
    run()
