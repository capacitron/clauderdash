"""DEV_MODE seeding. Inserts synthetic data so every panel renders at first boot.

Activated by CLAUDE_DASHBOARD_DEV_MODE=1. Safe to re-run — uses INSERT OR IGNORE.
"""
from __future__ import annotations

import datetime as dt
import random
import uuid
from pathlib import Path
from ..db import connect

_UTC = dt.timezone.utc

_TOOLS = ["Read", "Edit", "Write", "Bash", "Grep", "Glob", "WebFetch", "TaskCreate"]
_MCP_TOOLS = [
    ("jcodemunch", "index_folder"),
    ("jcodemunch", "search_code"),
    ("notion", "update_page"),
    ("notion", "search"),
    ("obsidian", "search_vault"),
    ("firecrawl", "scrape"),
]
_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]
_SKILLS = ["plan", "review", "ship", "ralph", "build", "yt", "decide", "design-system"]
_HOME = str(Path.home())
_HOME_SLUG = _HOME.replace("/", "-").lstrip("-")
_PROJECTS = [
    (f"{_HOME}/projects/myapp", f"-{_HOME_SLUG}-projects-myapp"),
    (f"{_HOME}/projects/api", f"-{_HOME_SLUG}-projects-api"),
    (_HOME, f"-{_HOME_SLUG}"),
    (f"{_HOME}/projects/scripts", f"-{_HOME_SLUG}-projects-scripts"),
]


def _iso(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_UTC)
    return ts.astimezone(_UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def seed(n_sessions: int = 50, events_per_session: int = 20) -> int:
    conn = connect()
    try:
        count = 0
        now = dt.datetime.now(_UTC)
        for i in range(n_sessions):
            sid = "dev-" + uuid.uuid4().hex[:12]
            cwd, project_hash = random.choice(_PROJECTS)
            model = random.choices(_MODELS, weights=[1, 5, 3])[0]
            # v2: spread across 84 days so heatmap renders a full 12-week window
            start = now - dt.timedelta(days=random.randint(0, 83), hours=random.randint(0, 23))
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    id, project_hash, cwd, model, started_at, last_event_at,
                    last_user_message, last_modified_ts, outcome, entrypoint
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'cli')
                """,
                (
                    sid,
                    project_hash,
                    cwd,
                    model,
                    _iso(start),
                    _iso(start + dt.timedelta(minutes=random.randint(5, 180))),
                    random.choice([
                        "implement the dashboard backend",
                        "fix the migration safety check",
                        "/plan roll out new feature flag",
                        "why is my build failing on railway",
                    ]),
                    start.timestamp(),
                    random.choices(["ok", "errored", "unfinished"], weights=[8, 1, 1])[0],
                ),
            )
            for j in range(events_per_session):
                event_ts = start + dt.timedelta(minutes=random.randint(0, 120))
                is_mcp = random.random() < 0.3
                if is_mcp:
                    server, tool = random.choice(_MCP_TOOLS)
                    name = f"mcp__{server}__{tool}"
                    base_ms = random.choice([80, 400, 1500, 3000, 12_000])
                else:
                    name = random.choice(_TOOLS)
                    server = tool = None
                    base_ms = random.choice([30, 120, 300, 900])
                duration = max(5, int(base_ms * random.uniform(0.4, 1.8)))
                tool_use_id = "tu_" + uuid.uuid4().hex[:16]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tool_calls(
                        tool_use_id, session_id, tool_name, is_mcp, mcp_server, mcp_tool,
                        started_at, ended_at, duration_ms, is_error
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tool_use_id,
                        sid,
                        name,
                        1 if is_mcp else 0,
                        server,
                        tool,
                        _iso(event_ts),
                        _iso(event_ts + dt.timedelta(milliseconds=duration)),
                        duration,
                        1 if random.random() < 0.05 else 0,
                    ),
                )
                count += 1

                muuid = "m_" + uuid.uuid4().hex[:16]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO messages(
                        uuid, session_id, role, model, ts,
                        input_tokens, output_tokens, cache_read_tokens, cache_create_tokens
                    ) VALUES(?, ?, 'assistant', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        muuid,
                        sid,
                        model,
                        _iso(event_ts),
                        random.randint(100, 2_000),
                        random.randint(50, 1_500),
                        random.randint(5_000, 30_000),
                        random.randint(500, 5_000),
                    ),
                )

            # Maybe add a skill invocation
            if random.random() < 0.5:
                conn.execute(
                    """
                    INSERT INTO skill_invocations(session_id, skill_name, ts)
                    VALUES(?, ?, ?)
                    """,
                    (sid, random.choice(_SKILLS), _iso(start)),
                )

        # v2: seed recurring error fingerprints for two-strike panel
        _seed_repeated_errors(conn, now)

        # v3 pass_2_a: seed messages in recent windows so mission clock renders
        _seed_mission_clock_window(conn, now)
        conn.commit()
        return count
    finally:
        conn.close()


def _seed_mission_clock_window(conn, now: dt.datetime) -> None:
    """Seed sessions + messages in last 2h (5h window) and last 3d (weekly window)
    so mission clock renders non-zero data in DEV_MODE."""
    # Last 2h: 3 sessions, each with 3-8 assistant messages
    for _ in range(3):
        sid = "dev-mc-" + uuid.uuid4().hex[:10]
        cwd, project_hash = random.choice(_PROJECTS)
        start = now - dt.timedelta(minutes=random.randint(5, 120))
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions(
                id, project_hash, cwd, model, started_at, last_event_at,
                last_modified_ts, outcome, entrypoint
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'ok', 'cli')
            """,
            (sid, project_hash, cwd, random.choice(_MODELS),
             _iso(start), _iso(start), start.timestamp()),
        )
        for _ in range(random.randint(3, 8)):
            conn.execute(
                """
                INSERT OR IGNORE INTO messages(
                    uuid, session_id, role, model, ts,
                    input_tokens, output_tokens, cache_read_tokens, cache_create_tokens
                ) VALUES(?, ?, 'assistant', ?, ?, ?, ?, ?, ?)
                """,
                (
                    "m-mc-" + uuid.uuid4().hex[:16],
                    sid,
                    random.choice(_MODELS),
                    _iso(start + dt.timedelta(seconds=random.randint(1, 3600))),
                    random.randint(500, 5000),
                    random.randint(200, 2000),
                    random.randint(10000, 50000),
                    random.randint(1000, 10000),
                ),
            )

    # Last 3d: 5 sessions, one message each (for weekly window signal)
    for _ in range(5):
        sid = "dev-mc-w-" + uuid.uuid4().hex[:8]
        cwd, project_hash = random.choice(_PROJECTS)
        start = now - dt.timedelta(days=random.randint(1, 3), hours=random.randint(0, 23))
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions(
                id, project_hash, cwd, model, started_at, last_event_at,
                last_modified_ts, outcome, entrypoint
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'ok', 'cli')
            """,
            (sid, project_hash, cwd, random.choice(_MODELS),
             _iso(start), _iso(start), start.timestamp()),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO messages(
                uuid, session_id, role, model, ts,
                input_tokens, output_tokens, cache_read_tokens, cache_create_tokens
            ) VALUES(?, ?, 'assistant', ?, ?, ?, ?, ?, ?)
            """,
            ("m-mc-w-" + uuid.uuid4().hex[:14], sid, random.choice(_MODELS),
             _iso(start),
             random.randint(1000, 10000), random.randint(500, 5000),
             random.randint(20000, 100000), random.randint(2000, 20000)),
        )


_ERROR_FIXTURES: list[tuple[str, str, int]] = [
    ("Bash", "FileNotFoundError: no such file or directory: /tmp/fixture", 4),
    ("WebFetch", "Request failed with status code 404", 4),
    ("Read", f"File does not exist. Note: your current working directory is {_HOME}.", 3),
]


def _seed_repeated_errors(conn, now: dt.datetime) -> None:
    """Insert 11 synthetic error tool_calls across 3 repeated error_preview values."""
    for tool_name, preview, count in _ERROR_FIXTURES:
        for _ in range(count):
            sid = "dev-" + uuid.uuid4().hex[:12]
            cwd, project_hash = random.choice(_PROJECTS)
            start = now - dt.timedelta(days=random.randint(0, 7), hours=random.randint(0, 23))
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    id, project_hash, cwd, model, started_at, last_event_at,
                    last_modified_ts, outcome, entrypoint
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'errored', 'cli')
                """,
                (
                    sid,
                    project_hash,
                    cwd,
                    random.choice(_MODELS),
                    _iso(start),
                    _iso(start),
                    start.timestamp(),
                ),
            )
            tu = "tu_" + uuid.uuid4().hex[:16]
            conn.execute(
                """
                INSERT OR IGNORE INTO tool_calls(
                    tool_use_id, session_id, tool_name, is_mcp,
                    started_at, ended_at, duration_ms, is_error, error_preview
                ) VALUES(?, ?, ?, 0, ?, ?, ?, 1, ?)
                """,
                (
                    tu,
                    sid,
                    tool_name,
                    _iso(start),
                    _iso(start + dt.timedelta(seconds=1)),
                    random.randint(50, 2000),
                    preview,
                ),
            )
