"""Session yield analysis. Correlates sessions with git commits.

For each completed session, checks if a commit was made in the session's cwd
during the session window (+1h grace). Three categories:
  productive — commits landed in main/master
  reverted   — >50% of commits in window are reverts
  abandoned  — no commits in window
  non_git    — cwd is not a git repo or git not installed
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_ANALYZE_INTERVAL_S = 5 * 60  # 5 minutes
_SESSION_GRACE_S    = 3600    # +1h after last event


def _run_git(cmd: list[str], cwd: str) -> str | None:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _is_git_repo(cwd: str) -> bool:
    return _run_git(["git", "rev-parse", "--is-inside-work-tree"], cwd) == "true"


def _get_main_branch(cwd: str) -> str:
    result = _run_git(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd)
    if result:
        return result.replace("refs/remotes/origin/", "")
    branches = _run_git(["git", "branch", "-a"], cwd) or ""
    if "main" in branches:
        return "main"
    return "master"


def _get_commits_in_window(cwd: str, since: str, until: str, main_branch: str) -> list[dict]:
    log_out = _run_git(
        ["git", "log", "--all", f"--since={since}", f"--until={until}", "--format=%H|%aI|%s"],
        cwd,
    )
    if not log_out:
        return []

    main_log = _run_git(["git", "log", main_branch, "--format=%H"], cwd) or ""
    main_shas = set(main_log.splitlines())

    commits = []
    for line in log_out.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, ts, subject = parts
        commits.append({
            "sha": sha,
            "ts": ts,
            "is_revert": "revert" in subject.lower(),
            "in_main": sha in main_shas,
        })
    return commits


def _categorize(commits: list[dict]) -> tuple[str, str | None]:
    """Returns (category, first_commit_sha)."""
    if not commits:
        return "abandoned", None
    in_main = [c for c in commits if c["in_main"]]
    reverted = [c for c in in_main if c["is_revert"]]
    first_sha = commits[0]["sha"] if commits else None
    if not in_main:
        return "abandoned", first_sha
    if reverted and len(reverted) >= len(in_main) / 2:
        return "reverted", first_sha
    return "productive", first_sha


def analyze_pending_sessions(conn: sqlite3.Connection) -> int:
    """Process sessions where yielded IS NULL and outcome is final. Returns count."""
    rows = conn.execute(
        """
        SELECT id, cwd, started_at, last_event_at
          FROM sessions
         WHERE outcome IN ('ok', 'errored')
           AND yielded IS NULL
           AND cwd IS NOT NULL
           AND started_at IS NOT NULL
        """
    ).fetchall()

    updated = 0
    for row in rows:
        session_id = row["id"]
        cwd        = row["cwd"]
        started    = row["started_at"]
        last_event = row["last_event_at"] or started

        if not _is_git_repo(cwd):
            conn.execute(
                "UPDATE sessions SET yielded = -1 WHERE id = ?", (session_id,)
            )
            updated += 1
            continue

        # Parse window end (+1h grace)
        try:
            end_dt = datetime.fromisoformat(last_event.replace("Z", "+00:00"))
            end_ts = end_dt.timestamp() + _SESSION_GRACE_S
            end_iso = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
        except Exception:
            end_iso = last_event

        main_branch = _get_main_branch(cwd)
        commits = _get_commits_in_window(cwd, started, end_iso, main_branch)
        category, first_sha = _categorize(commits)

        yielded_val = 1 if category == "productive" else (2 if category == "reverted" else 0)
        conn.execute(
            "UPDATE sessions SET yielded = ?, yield_commit = ? WHERE id = ?",
            (yielded_val, first_sha, session_id),
        )
        updated += 1

    if updated:
        conn.commit()
    return updated


def _connect_and_analyze() -> int:
    # sqlite3 connections are single-thread-affine: opening one on the event
    # loop's thread and handing it to run_in_executor's worker thread crashes
    # with "SQLite objects created in a thread can only be used in that same
    # thread" on every tick. connect() must happen on the SAME thread that
    # runs the query, so both live inside the executor call.
    from ..db import connect

    conn = connect()
    try:
        return analyze_pending_sessions(conn)
    finally:
        conn.close()


async def run_yield_analyzer() -> None:
    """Background task: check for unanalyzed sessions every 5 minutes."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(_ANALYZE_INTERVAL_S)
        try:
            n = await loop.run_in_executor(None, _connect_and_analyze)
            if n:
                log.info("yield: analyzed %d sessions", n)
        except Exception:
            log.exception("yield analyzer failed")
