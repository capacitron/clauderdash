#!/usr/bin/env python3
"""Zero out duplicated usage on multi-line assistant messages.

Claude Code writes one transcript line per content block; every line of the
same API request repeats the identical usage object. Ingest stored each line
as its own row with full usage, so tokens and cost are inflated ~2-3x (the
multiplier is the average content-block count, which differs per model).

Two passes:
  1. Exact — re-scan surviving raw transcripts, group rows by requestId,
     keep usage on the first row of each request, zero the siblings. Also
     backfills messages.request_id for every matched row.
  2. Heuristic — rows whose raw transcript is already deleted. Within a
     (session, agent_type, model) partition ordered by ts, a row whose
     (input, output, cache_read, cache_create, cache_1h) exactly equals a
     kept row's within 300s is a duplicate line of the same request.
     ponytail: exact-quad+proximity match; cache_read makes collisions
     between distinct requests effectively impossible once caching is on.

Then rebuilds sessions.cost_usd for affected sessions only (provider
sessions carry their own rollup and are never touched).

The server holds a write lock on the DB. Stop it first:
    systemctl --user stop claude-dashboard

Usage:
    python3 scripts/dedupe_usage.py --dry-run     # report only, no writes
    python3 scripts/dedupe_usage.py               # apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import DB_PATH  # noqa: E402

TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"
WINDOW_S = 300  # blocks of one streamed response land within this


def scan_transcripts() -> dict[str, list[str]]:
    """requestId -> [uuid, ...] in encounter order, assistant lines with usage."""
    groups: dict[str, list[str]] = {}
    seen: set[str] = set()
    for path in sorted(TRANSCRIPT_ROOT.rglob("*.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("type") != "assistant":
                        continue
                    uuid = e.get("uuid")
                    if not uuid or uuid in seen:
                        continue
                    msg = e.get("message") or {}
                    u = msg.get("usage") or {}
                    if not any(
                        u.get(k)
                        for k in (
                            "input_tokens",
                            "output_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        )
                    ):
                        continue
                    req = e.get("requestId") or msg.get("id")
                    if not req:
                        continue
                    seen.add(uuid)
                    groups.setdefault(req, []).append(uuid)
        except OSError:
            continue
    return groups


def _epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row

    # The server's init_db() normally adds this column; running the migration
    # before the updated server has ever started must not depend on that.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if "request_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN request_id TEXT")
        conn.commit()

    before = conn.execute(
        "SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
        "COALESCE(SUM(cache_read_tokens),0) cr, COALESCE(SUM(cost_usd),0) c "
        "FROM messages WHERE role='assistant'"
    ).fetchone()

    # ---- pass 1: exact, from raw transcripts -------------------------------
    print("scanning raw transcripts ...", flush=True)
    groups = scan_transcripts()
    matched_uuids: set[str] = set()
    zero_exact: list[str] = []
    req_backfill: list[tuple[str, str]] = []
    for req, uuids in groups.items():
        for n, uuid in enumerate(uuids):
            matched_uuids.add(uuid)
            req_backfill.append((req, uuid))
            if n > 0:
                zero_exact.append(uuid)
    print(f"raw: {len(groups)} requests, {len(matched_uuids)} lines, "
          f"{len(zero_exact)} duplicate lines to zero")

    # ---- pass 2: heuristic, rows whose raw file is gone --------------------
    rows = conn.execute(
        """
        SELECT uuid, session_id, COALESCE(agent_type,'') at, COALESCE(model,'') model, ts,
               input_tokens i, output_tokens o, cache_read_tokens cr,
               cache_create_tokens cw, cache_create_1h_tokens cw1
          FROM messages
         WHERE role='assistant'
           AND (input_tokens > 0 OR output_tokens > 0 OR cache_read_tokens > 0
                OR cache_create_tokens > 0)
      ORDER BY session_id, at, model, ts
        """
    ).fetchall()

    zero_heur: list[str] = []
    part_key = None
    kept: list[tuple[float, tuple[int, int, int, int, int]]] = []
    for r in rows:
        if r["uuid"] in matched_uuids:
            continue
        key = (r["session_id"], r["at"], r["model"])
        if key != part_key:
            part_key = key
            kept = []
        t = _epoch(r["ts"])
        quad = (r["i"], r["o"], r["cr"], r["cw"], r["cw1"])
        kept = [(kt, kq) for kt, kq in kept if t - kt <= WINDOW_S]
        if any(kq == quad for _, kq in kept):
            zero_heur.append(r["uuid"])
        else:
            kept.append((t, quad))
    print(f"heuristic: {len(zero_heur)} duplicate lines to zero "
          f"(of {len(rows)} usage-bearing rows)")

    zero_all = zero_exact + zero_heur
    if args.dry_run:
        est = conn.execute(
            f"SELECT COALESCE(SUM(output_tokens),0) o, COALESCE(SUM(cost_usd),0) c "
            f"FROM messages WHERE uuid IN ({','.join('?' * len(zero_all))})",
            zero_all,
        ).fetchone() if zero_all else {"o": 0, "c": 0}
        print(f"dry-run: would zero {len(zero_all)} rows "
              f"({est['o']/1e6:.1f}M output tokens, ${est['c']:.2f})")
        conn.close()
        return

    # ---- apply -------------------------------------------------------------
    affected = {
        r["session_id"]
        for chunk in range(0, len(zero_all), 500)
        for r in conn.execute(
            f"SELECT DISTINCT session_id FROM messages "
            f"WHERE uuid IN ({','.join('?' * len(zero_all[chunk:chunk + 500]))})",
            zero_all[chunk:chunk + 500],
        )
    }
    for chunk in range(0, len(zero_all), 500):
        batch = zero_all[chunk:chunk + 500]
        conn.execute(
            f"""UPDATE messages
                   SET input_tokens=0, output_tokens=0, cache_read_tokens=0,
                       cache_create_tokens=0, cache_create_1h_tokens=0, cost_usd=0
                 WHERE uuid IN ({','.join('?' * len(batch))})""",
            batch,
        )
    conn.executemany(
        "UPDATE messages SET request_id = ? WHERE uuid = ? AND request_id IS NULL",
        req_backfill,
    )
    for sid in affected:
        conn.execute(
            "UPDATE sessions SET cost_usd = "
            "(SELECT COALESCE(SUM(cost_usd),0) FROM messages WHERE session_id = ?) "
            "WHERE id = ?",
            (sid, sid),
        )
    conn.commit()

    after = conn.execute(
        "SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
        "COALESCE(SUM(cache_read_tokens),0) cr, COALESCE(SUM(cost_usd),0) c "
        "FROM messages WHERE role='assistant'"
    ).fetchone()
    conn.close()

    print(f"zeroed {len(zero_all)} rows, rebuilt {len(affected)} session rollups")
    print(f"input  {before['i']/1e6:10.1f}M -> {after['i']/1e6:10.1f}M")
    print(f"output {before['o']/1e6:10.1f}M -> {after['o']/1e6:10.1f}M")
    print(f"cache  {before['cr']/1e9:10.2f}B -> {after['cr']/1e9:10.2f}B")
    print(f"cost   ${before['c']:9.2f} -> ${after['c']:9.2f}")


if __name__ == "__main__":
    main()
