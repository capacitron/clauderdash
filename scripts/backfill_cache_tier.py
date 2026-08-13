#!/usr/bin/env python3
"""Backfill the 1h/5m cache-write split onto historical rows and reprice them.

Rows ingested before the split was tracked priced every cache write at the 5m
rate (1.25x base input). Anthropic charges 2x for the 1h tier, and long Opus
sessions write almost exclusively at 1h, so the old figures under-state exactly
the models the CLAUDE.md spend targets are watching.

Reads the raw transcripts, matches on message uuid (never on file order), writes
cache_create_1h_tokens, recomputes cost_usd, then rebuilds the sessions rollup.

The server holds a write lock on the DB. Stop it first:
    systemctl --user stop claude-dashboard

Usage:
    python3 scripts/backfill_cache_tier.py --dry-run     # report only, no writes
    python3 scripts/backfill_cache_tier.py               # apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import DB_PATH  # noqa: E402
from app.ingest.pricing import get_cost_usd, load_pricing_sync  # noqa: E402

TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"


def scan_transcripts() -> dict[str, int]:
    """uuid -> 1h cache-write tokens, for every assistant message that has any."""
    found: dict[str, int] = {}
    for path in TRANSCRIPT_ROOT.rglob("*.jsonl"):
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                # Cheap reject before the JSON parse; most lines are not assistant turns.
                if "ephemeral_1h_input_tokens" not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                uuid = event.get("uuid")
                usage = (event.get("message") or {}).get("usage") or {}
                tokens = int((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0) or 0)
                if uuid and tokens:
                    found[uuid] = tokens
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    load_pricing_sync()

    print(f"Scanning {TRANSCRIPT_ROOT} ...")
    tiers = scan_transcripts()
    print(f"  {len(tiers):,} messages carry a 1h cache write")
    if not tiers:
        return 0

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")

    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if "cache_create_1h_tokens" not in cols:
        print("ERROR: messages.cache_create_1h_tokens is missing. Start the server once "
              "so init_db() applies the migration, then stop it and re-run.", file=sys.stderr)
        return 1

    updates: list[tuple[int, float, str]] = []
    sessions: set[str] = set()
    delta = 0.0

    placeholders = list(tiers)
    for start in range(0, len(placeholders), 500):
        chunk = placeholders[start:start + 500]
        rows = conn.execute(
            f"SELECT uuid, session_id, model, input_tokens, output_tokens, "
            f"cache_read_tokens, cache_create_tokens, cost_usd "
            f"FROM messages WHERE uuid IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for row in rows:
            if not row["model"]:
                continue
            cw1h = min(tiers[row["uuid"]], row["cache_create_tokens"] or 0)
            new_cost = get_cost_usd(
                row["model"],
                row["input_tokens"] or 0,
                row["output_tokens"] or 0,
                row["cache_read_tokens"] or 0,
                row["cache_create_tokens"] or 0,
                cw1h,
            )
            delta += new_cost - (row["cost_usd"] or 0.0)
            updates.append((cw1h, new_cost, row["uuid"]))
            sessions.add(row["session_id"])

    print(f"  {len(updates):,} DB rows matched across {len(sessions):,} sessions")
    print(f"  cost delta: ${delta:+,.2f}")

    if args.dry_run:
        print("dry run, nothing written")
        conn.close()
        return 0

    conn.executemany(
        "UPDATE messages SET cache_create_1h_tokens = ?, cost_usd = ? WHERE uuid = ?",
        updates,
    )
    # Rebuild the rollup from the rows rather than adding the delta, so any
    # pre-existing drift between sessions.cost_usd and its messages is corrected too.
    conn.executemany(
        "UPDATE sessions SET cost_usd = COALESCE(("
        "  SELECT SUM(cost_usd) FROM messages WHERE messages.session_id = sessions.id"
        "), 0) WHERE id = ?",
        [(s,) for s in sessions],
    )
    conn.commit()
    conn.close()
    print("done. restart: systemctl --user start claude-dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
