"""Regression test for a thread-affinity crash in the yield analyzer and
optimize scanner background tasks.

Both used to open their sqlite3 connection on the asyncio event loop's thread
and hand it to `loop.run_in_executor(None, fn, conn)`, which runs `fn` on a
different (threadpool worker) thread. sqlite3 connections are single-thread
affine by default, so every 5-minute (yield) / 30-minute (optimize) tick
crashed with:

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

silently, forever — caught by a bare `except Exception: log.exception(...)`
in the polling loop, so the process kept running and looked healthy while
neither background task ever completed a single real cycle. Fixed by moving
connect() inside the function that actually runs in the executor thread, so
open and use happen on the same thread. This test reproduces the exact
`run_in_executor` call shape that crashed before the fix.
"""
from __future__ import annotations

import asyncio

# Other test files get schema.sql applied as a side effect of constructing a
# TestClient(app), which runs FastAPI's startup event. This file never
# touches the HTTP app, so it must call init_db() itself or every query 404s
# on "no such table: sessions" against conftest's empty temp DB.
from app.db import init_db

init_db()


def test_yield_analyzer_survives_real_executor_thread() -> None:
    from app.ingest.yield_analyzer import _connect_and_analyze

    async def run():
        loop = asyncio.get_event_loop()
        # Must actually cross threads via run_in_executor — calling
        # _connect_and_analyze() directly on the test's thread would not
        # reproduce the bug at all.
        return await loop.run_in_executor(None, _connect_and_analyze)

    result = asyncio.run(run())
    assert isinstance(result, int)


def test_optimize_scanner_survives_real_executor_thread() -> None:
    from app.ingest.optimize import _connect_and_scan

    async def run():
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _connect_and_scan)

    result = asyncio.run(run())
    assert isinstance(result, int)
