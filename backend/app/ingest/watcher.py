"""Background loop that keeps the JSONL cache warm.

Implementation: simple poll every 2s. watchdog would work too but polling is:
  - dependency-light in practice (os.stat is cheap)
  - reliable across editors/filesystems
  - trivially resumable after service restarts
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..config import CLAUDE_PROJECTS_DIR
from . import jsonl
from .providers.sync import sync_all_providers

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 5.0
RECOMPUTE_EVERY_S = 30.0


async def run_watcher() -> None:
    """Continuous ingestion loop. Runs inside the FastAPI event loop."""
    loop = asyncio.get_event_loop()
    last_recompute = 0.0
    # Warm start
    log.info("Initial JSONL backfill starting...")
    result = await loop.run_in_executor(None, jsonl.sync_all)
    log.info(
        "Initial backfill complete: %d files, %d events", result["files"], result["events"]
    )
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            await loop.run_in_executor(None, _tick)
            if time.time() - last_recompute > RECOMPUTE_EVERY_S:
                await loop.run_in_executor(None, _recompute)
                last_recompute = time.time()
        except Exception:  # keep the loop alive
            log.exception("watcher tick failed")


# (mtime, size) of every file as of its last successful sync. Without this the
# poll re-opens a SQLite connection and commits a no-op write for all 2000+
# files every tick. Set only after sync_file succeeds so failures retry.
_seen: dict[str, tuple[float, int]] = {}


def _tick() -> None:
    if CLAUDE_PROJECTS_DIR.exists():
        for path in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            sig = (st.st_mtime, st.st_size)
            if _seen.get(str(path)) == sig:
                continue
            jsonl.sync_file(path)
            _seen[str(path)] = sig
    _tick_providers()


def _tick_providers() -> None:
    from ..db import connect
    conn = connect()
    try:
        sync_all_providers(conn)
    finally:
        conn.close()


def _recompute() -> None:
    import datetime as dt
    from ..db import connect
    from .classifier import classify_all_sessions

    conn = connect()
    try:
        jsonl._recompute_outcomes(conn)
        conn.commit()
        classify_all_sessions(conn)
        # Prune otel_events older than 7 days (prevents unbounded growth)
        cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()
        conn.execute("DELETE FROM otel_events WHERE received_at < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()
