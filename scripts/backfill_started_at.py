#!/usr/bin/env python3
"""Fill sessions.started_at for rows the old upsert pinned NULL.

_upsert_session listed started_at in its INSERT columns and left it out of the
ON CONFLICT SET clause, so a session whose first-seen event carried no parseable
timestamp kept started_at NULL no matter how many later events arrived.

The consequence was not cosmetic. yield_analyzer's pending query filters on
`started_at IS NOT NULL`, so these sessions were permanently invisible to
outcome attribution rather than merely queued — roughly 934 sessions holding
close to half of all-time recorded spend.

jsonl.py now heals this going forward. This script repairs the existing rows,
deriving each session's start from the earliest message it actually has. A
session with no messages at all is left NULL rather than guessed at.

Additive only: fills NULLs, never overwrites an existing value. The affected
ids are written to a revert file before anything is changed.

The server holds a write lock on the DB. Stop it first:
    systemctl --user stop claude-dashboard

Usage:
    python3 scripts/backfill_started_at.py --dry-run     # report only, no writes
    python3 scripts/backfill_started_at.py               # apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import DATA_DIR, DB_PATH  # noqa: E402

# Earliest message timestamp per session, for sessions missing a start. The
# EXISTS guard keeps sessions with no messages out of the update entirely, so
# they stay NULL instead of being set to NULL a second time.
SELECT_REPAIRABLE = """
    SELECT s.id, MIN(m.ts) AS first_ts, COUNT(m.uuid) AS n_messages
    FROM sessions s
    JOIN messages m ON m.session_id = s.id
    WHERE s.started_at IS NULL AND m.ts IS NOT NULL
    GROUP BY s.id
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")

    total_null = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE started_at IS NULL"
    ).fetchone()[0]
    rows = conn.execute(SELECT_REPAIRABLE).fetchall()

    print(f"  {total_null:,} sessions have started_at IS NULL")
    print(f"  {len(rows):,} of them have messages to derive a start from")
    print(f"  {total_null - len(rows):,} have no usable message timestamp, left NULL")

    if not rows:
        conn.close()
        return 0

    earliest = min(r["first_ts"] for r in rows)
    latest = max(r["first_ts"] for r in rows)
    print(f"  derived starts span {earliest} .. {latest}")

    if args.dry_run:
        for row in rows[:5]:
            print(f"    {row['id']}  ->  {row['first_ts']}  ({row['n_messages']:,} messages)")
        if len(rows) > 5:
            print(f"    ... and {len(rows) - 5:,} more")
        print("dry run, nothing written")
        conn.close()
        return 0

    # Revert path: these ids were NULL before this run, so restoring them is
    # `UPDATE sessions SET started_at = NULL WHERE id IN (<this file>)`.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    revert = DATA_DIR / f"backfill_started_at_revert_{stamp}.txt"
    revert.write_text("\n".join(r["id"] for r in rows) + "\n")
    print(f"  revert list written to {revert}")

    conn.executemany(
        "UPDATE sessions SET started_at = ? WHERE id = ? AND started_at IS NULL",
        [(r["first_ts"], r["id"]) for r in rows],
    )
    conn.commit()

    remaining = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE started_at IS NULL"
    ).fetchone()[0]
    conn.close()

    print(f"  {len(rows):,} sessions repaired, {remaining:,} still NULL")
    print("done. yield_analyzer picks these up on its next 5-minute tick.")
    print("restart: systemctl --user start claude-dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
