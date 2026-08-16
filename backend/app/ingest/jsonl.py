"""JSONL ingestion. Walks Claude Code's per-session log files, incrementally.

Each session is one file: ~/.claude/projects/<project-hash>/<session-uuid>.jsonl
Every line is a JSON object. Types we care about:

  user      — message.content is either str or [{type, text/tool_result, ...}]
              if content includes a command-name tag, it's a slash-command invocation
  assistant — message.model + message.usage + message.content[]
              tool_use blocks live inside content[] alongside text / thinking
  tool_result — lives inside user.message.content[] as {tool_use_id, is_error, ...}
  system    — CLI command echoes, ignored for panels
  result    — session-end summary if present

We track source_offset per file so restarts are incremental.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from ..config import (
    CLAUDE_PROJECTS_DIR,
    LIVE_WINDOW_SECONDS,
    MAX_TOOL_DURATION_MS,
)
from ..db import connect
from .pricing import get_cost_usd

log = logging.getLogger(__name__)

_COMMAND_NAME_RE = re.compile(r"<command-name>([^<]+)</command-name>")
_SLASH_LINE_RE = re.compile(r"^\s*/([A-Za-z][A-Za-z0-9_:\-]*)")


def _parse_ts(value: str | None) -> str | None:
    """Normalise timestamp. Return None if missing/garbage."""
    if not value:
        return None
    return value  # ISO8601 already; SQLite orders lexicographically fine


def _millis_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    import datetime as dt

    try:
        s = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta_ms = int((e - s).total_seconds() * 1000)
    if delta_ms < 0:
        return None
    if delta_ms > MAX_TOOL_DURATION_MS:
        return MAX_TOOL_DURATION_MS
    return delta_ms


def _extract_slash_command(content: Any) -> str | None:
    """Return skill/slash-command name, or None."""
    if isinstance(content, str):
        m = _COMMAND_NAME_RE.search(content)
        if m:
            name = m.group(1).strip().lstrip("/")
            return name or None
        m2 = _SLASH_LINE_RE.match(content)
        if m2:
            return m2.group(1)
        return None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                found = _extract_slash_command(block.get("text", ""))
                if found:
                    return found
    return None


def _extract_user_text(content: Any) -> str:
    """First 200 chars of the user's actual text, skipping tool_result blocks."""
    if isinstance(content, str):
        return content[:200]
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                return str(block.get("text", ""))[:200]
    return ""


def _is_mcp_tool(name: str) -> tuple[bool, str | None, str | None]:
    if not name.startswith("mcp__"):
        return False, None, None
    parts = name.split("__", 2)
    if len(parts) < 3:
        return True, parts[1] if len(parts) > 1 else None, None
    return True, parts[1], parts[2]


def _upsert_session(
    conn: sqlite3.Connection,
    session_id: str,
    project_hash: str,
    cwd: str | None,
    model: str | None,
    ts: str | None,
    entrypoint: str | None,
    version: str | None,
    mtime: float,
) -> None:
    conn.execute(
        """
        INSERT INTO sessions(
            id, project_hash, cwd, model, started_at, last_event_at,
            entrypoint, version, last_modified_ts
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            cwd = COALESCE(excluded.cwd, sessions.cwd),
            model = COALESCE(excluded.model, sessions.model),
            last_event_at = COALESCE(excluded.last_event_at, sessions.last_event_at),
            entrypoint = COALESCE(excluded.entrypoint, sessions.entrypoint),
            version = COALESCE(excluded.version, sessions.version),
            last_modified_ts = MAX(COALESCE(sessions.last_modified_ts, 0), excluded.last_modified_ts)
        """,
        (
            session_id,
            project_hash,
            cwd,
            model,
            ts,
            ts,
            entrypoint,
            version,
            mtime,
        ),
    )


def _process_event(
    conn: sqlite3.Connection,
    event: dict,
    project_hash: str,
    mtime: float,
    agent_type: str | None = None,
) -> None:
    etype = event.get("type")
    session_id = event.get("sessionId")
    if not session_id:
        return
    ts = _parse_ts(event.get("timestamp"))
    cwd = event.get("cwd")
    entrypoint = event.get("entrypoint")
    version = event.get("version")

    msg = event.get("message") or {}
    model = msg.get("model") if isinstance(msg, dict) else None

    _upsert_session(
        conn, session_id, project_hash, cwd, model, ts,
        entrypoint, version, mtime,
    )

    # Bump event_count and last_event_at on every row
    conn.execute(
        "UPDATE sessions SET event_count = event_count + 1, last_event_at = ? WHERE id = ?",
        (ts, session_id),
    )

    if etype == "user":
        _handle_user(conn, event, session_id, agent_type)
    elif etype == "assistant":
        _handle_assistant(conn, event, session_id, agent_type)


def _handle_user(conn: sqlite3.Connection, event: dict, session_id: str, agent_type: str | None = None) -> None:
    msg = event.get("message") or {}
    content = msg.get("content")
    uuid = event.get("uuid")
    ts = _parse_ts(event.get("timestamp"))

    # Persist message row for completeness (tokens = 0 for user messages)
    preview = _extract_user_text(content)
    if uuid and ts:
        conn.execute(
            """
            INSERT OR IGNORE INTO messages(
                uuid, session_id, parent_uuid, role, ts, text_preview, agent_type
            ) VALUES(?, ?, ?, 'user', ?, ?, ?)
            """,
            (uuid, session_id, event.get("parentUuid"), ts, preview, agent_type),
        )
        # Keep latest user prompt on the session
        if preview and not event.get("isMeta"):
            conn.execute(
                "UPDATE sessions SET last_user_message = ? WHERE id = ?",
                (preview, session_id),
            )

    # Slash command → skill invocation
    slash = _extract_slash_command(content)
    if slash and not event.get("isMeta"):
        conn.execute(
            """
            INSERT INTO skill_invocations(session_id, message_uuid, skill_name, ts)
            VALUES(?, ?, ?, ?)
            """,
            (session_id, uuid, slash, ts),
        )

    # Tool results live inside user content[]
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                _close_tool_call(
                    conn,
                    tool_use_id=block.get("tool_use_id"),
                    ended_at=ts,
                    is_error=bool(block.get("is_error")),
                    error_preview=_stringify_tool_result(block.get("content"))[:300]
                    if block.get("is_error")
                    else None,
                )
                if block.get("is_error"):
                    conn.execute(
                        "UPDATE sessions SET total_tool_errors = total_tool_errors + 1 "
                        "WHERE id = ?",
                        (session_id,),
                    )


def _handle_assistant(conn: sqlite3.Connection, event: dict, session_id: str, agent_type: str | None = None) -> None:
    msg = event.get("message") or {}
    uuid = event.get("uuid")
    ts = _parse_ts(event.get("timestamp"))
    usage = msg.get("usage") or {}
    model = msg.get("model")

    # Persist message row with token usage
    inp = int(usage.get("input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    cr  = int(usage.get("cache_read_input_tokens", 0) or 0)
    cw  = int(usage.get("cache_creation_input_tokens", 0) or 0)
    # cache_creation splits the write by TTL; the 1h tier costs 2x base input
    # against the 5m tier's 1.25x. Absent on older transcripts, which then price
    # entirely at the 5m rate exactly as they did before.
    cache_creation = usage.get("cache_creation") or {}
    cw1h = int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0)

    # One API call = one usage bill, but the transcript writes one line per
    # content block and repeats the identical usage object on every line.
    # Count it once: the first line of a request keeps the usage, siblings are
    # stored with zeros (the row itself stays — tool_calls hang off it).
    request_id = event.get("requestId") or msg.get("id")
    if request_id and (inp or out or cr or cw):
        dup = conn.execute(
            "SELECT 1 FROM messages WHERE request_id = ? AND "
            "(input_tokens > 0 OR output_tokens > 0 OR cache_read_tokens > 0 "
            "OR cache_create_tokens > 0) LIMIT 1",
            (request_id,),
        ).fetchone()
        if dup:
            inp = out = cr = cw = cw1h = 0

    cost = get_cost_usd(model or "", inp, out, cr, cw, cw1h) if model else 0.0

    if uuid and ts:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO messages(
                uuid, session_id, parent_uuid, role, model, ts,
                input_tokens, output_tokens, cache_read_tokens, cache_create_tokens,
                cache_create_1h_tokens, cost_usd, text_preview, agent_type, request_id
            ) VALUES(?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                uuid,
                session_id,
                event.get("parentUuid"),
                model,
                ts,
                inp,
                out,
                cr,
                cw,
                cw1h,
                cost,
                agent_type,
                request_id,
            ),
        )
        # rowcount guard: a truncation-reset rescan replays lines the uuid PK
        # already ignores — without it each replay re-adds the session cost.
        if cost > 0 and cur.rowcount > 0:
            conn.execute(
                "UPDATE sessions SET cost_usd = COALESCE(cost_usd, 0) + ? WHERE id = ?",
                (cost, session_id),
            )

    # tool_use blocks open tool_calls
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            tool_use_id = block.get("id")
            if not tool_use_id:
                continue
            name = str(block.get("name", ""))
            is_mcp, server, tool = _is_mcp_tool(name)
            input_preview = json.dumps(block.get("input") or {})[:500]
            conn.execute(
                """
                INSERT OR IGNORE INTO tool_calls(
                    tool_use_id, session_id, message_uuid, tool_name,
                    is_mcp, mcp_server, mcp_tool, input_preview, started_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_use_id,
                    session_id,
                    uuid,
                    name,
                    1 if is_mcp else 0,
                    server,
                    tool,
                    input_preview,
                    ts,
                ),
            )


def _close_tool_call(
    conn: sqlite3.Connection,
    tool_use_id: str | None,
    ended_at: str | None,
    is_error: bool,
    error_preview: str | None,
) -> None:
    if not tool_use_id:
        return
    row = conn.execute(
        "SELECT started_at FROM tool_calls WHERE tool_use_id = ?",
        (tool_use_id,),
    ).fetchone()
    if not row:
        return  # orphan — matching tool_use not yet seen
    duration = _millis_between(row["started_at"], ended_at)
    conn.execute(
        """
        UPDATE tool_calls
           SET ended_at = ?, duration_ms = ?, is_error = ?, error_preview = ?
         WHERE tool_use_id = ?
        """,
        (ended_at, duration, 1 if is_error else 0, error_preview, tool_use_id),
    )


def _stringify_tool_result(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if "text" in block:
                    parts.append(str(block["text"]))
                elif "content" in block:
                    parts.append(_stringify_tool_result(block["content"]))
        return " ".join(parts)
    return ""


def _recompute_outcomes(conn: sqlite3.Connection) -> None:
    """Cheap heuristic: errors in the tail = errored, long idle + no error = ok.

    OTEL when enabled gives us rate_limited / truncated; this is the JSONL-only path.
    """
    now = time.time()
    live_cutoff = now - LIVE_WINDOW_SECONDS
    # Live: mark unfinished
    conn.execute(
        "UPDATE sessions SET outcome = 'unfinished' WHERE last_modified_ts >= ?",
        (live_cutoff,),
    )
    # Not live + had errors in last 3 tool calls = errored
    conn.execute(
        """
        UPDATE sessions
           SET outcome = 'errored'
         WHERE last_modified_ts < ?
           AND id IN (
             SELECT session_id
               FROM (
                 SELECT session_id, is_error,
                        ROW_NUMBER() OVER (
                          PARTITION BY session_id
                          ORDER BY started_at DESC
                        ) AS rn
                 FROM tool_calls
                 WHERE started_at IS NOT NULL
               )
              WHERE rn <= 3 AND is_error = 1
           )
        """,
        (live_cutoff,),
    )
    # Everyone else = ok
    conn.execute(
        "UPDATE sessions SET outcome = 'ok' "
        "WHERE last_modified_ts < ? AND outcome NOT IN ('errored','rate_limited','truncated')",
        (live_cutoff,),
    )


def _iter_jsonl_lines(path: Path, start_offset: int) -> Iterable[tuple[int, dict]]:
    """Yield (end_offset, parsed_event). Skip malformed lines.

    Use readline() + explicit byte counting — `for line in file` has buffered
    read-ahead that makes f.tell() unreliable for offset tracking.
    """
    with path.open("rb") as f:
        f.seek(start_offset)
        pos = start_offset
        while True:
            raw = f.readline()
            if not raw:
                return
            pos += len(raw)
            try:
                event = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            yield pos, event


def _read_agent_type(path: Path) -> str | None:
    """agentType for a subagent transcript, read from its `.meta.json` sidecar.

    Detected by "subagents" appearing anywhere in the path rather than by
    position, because subagent files don't sit at a fixed depth — a plain
    Agent-tool run is `<session>/subagents/agent-<id>.jsonl` but a workflow
    run nests further (`<session>/subagents/workflows/<wf-id>/agent-<id>.jsonl`).
    Not every subagent run has a sidecar (older transcripts predate it), so a
    miss just means this row's agent_type stays NULL — same as a normal
    top-level message.
    """
    if "subagents" not in path.parts or path.suffix != ".jsonl":
        return None
    meta_path = path.with_name(path.name[: -len(".jsonl")] + ".meta.json")
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    agent_type = meta.get("agentType")
    return agent_type if isinstance(agent_type, str) else None


def sync_file(path: Path) -> int:
    """Ingest new bytes from one JSONL file. Returns count of events processed."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return 0
    mtime = stat.st_mtime
    size = stat.st_size

    # Subagent files sit under <session-id>/subagents/.
    # Treat the main project dir as project_hash for both kinds.
    project_hash = path.parent.name
    if project_hash == "subagents":
        project_hash = path.parent.parent.parent.name

    agent_type = _read_agent_type(path)

    conn = connect()
    try:
        row = conn.execute(
            "SELECT source_offset, session_id FROM file_cursors WHERE source_file = ?",
            (str(path),),
        ).fetchone()
        offset = int(row["source_offset"]) if row else 0
        if offset > size:
            offset = 0  # file was rotated/truncated
        if offset == size:
            # Still bump mtime so live-detection works even for files that
            # haven't grown (rare but possible when only metadata is updated).
            if row and row["session_id"]:
                conn.execute(
                    """
                    UPDATE sessions
                       SET last_modified_ts = MAX(COALESCE(last_modified_ts, 0), ?)
                     WHERE id = ?
                    """,
                    (mtime, row["session_id"]),
                )
                conn.execute(
                    "UPDATE file_cursors SET last_modified_ts = ? WHERE source_file = ?",
                    (mtime, str(path)),
                )
                conn.commit()
            return 0

        processed = 0
        last_offset = offset
        last_session_id: str | None = None
        for new_offset, event in _iter_jsonl_lines(path, offset):
            _process_event(conn, event, project_hash, mtime, agent_type)
            processed += 1
            last_offset = new_offset
            sid = event.get("sessionId")
            if sid:
                last_session_id = sid
        if last_session_id:
            conn.execute(
                """
                INSERT INTO file_cursors(source_file, session_id, source_offset, last_modified_ts)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(source_file) DO UPDATE SET
                    source_offset = excluded.source_offset,
                    last_modified_ts = excluded.last_modified_ts
                """,
                (str(path), last_session_id, last_offset, mtime),
            )
            # Also bump session mtime to max of all its files
            conn.execute(
                """
                UPDATE sessions
                   SET last_modified_ts = MAX(COALESCE(last_modified_ts, 0), ?)
                 WHERE id = ?
                """,
                (mtime, last_session_id),
            )
        conn.commit()
        return processed
    finally:
        conn.close()


def sync_all() -> dict[str, int]:
    """Full sweep across CLAUDE_PROJECTS_DIR. Used for first-run backfill + periodic refresh."""
    if not CLAUDE_PROJECTS_DIR.exists():
        log.warning("Claude projects dir not found: %s", CLAUDE_PROJECTS_DIR)
        return {"files": 0, "events": 0}

    total_events = 0
    total_files = 0
    for jsonl_path in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
        total_files += 1
        total_events += sync_file(jsonl_path)

    conn = connect()
    try:
        _recompute_outcomes(conn)
        conn.commit()
    finally:
        conn.close()

    return {"files": total_files, "events": total_events}
