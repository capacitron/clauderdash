"""Upsert ProviderSession rows into the sessions/messages/tool_calls schema."""
from __future__ import annotations

import logging
import uuid
from sqlite3 import Connection

from . import ProviderSession
from ..classifier import classify_session

log = logging.getLogger(__name__)


def sync_provider_session(conn: Connection, s: ProviderSession) -> bool:
    """Insert session if not already present. Returns True if new."""
    existing = conn.execute(
        "SELECT id FROM sessions WHERE id = ?", (s["session_id"],)
    ).fetchone()
    if existing:
        return False

    activity = classify_session(s["tools"], s["last_user_message"])
    one_shot = 1 if len(s["tools"]) <= 2 else 0

    conn.execute(
        """INSERT INTO sessions
           (id, project, cwd, model, started_at, last_event_at,
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
            cost_usd, activity_type, one_shot, provider, outcome)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ok')""",
        (
            s["session_id"],
            s["project"],
            s["cwd"],
            s["model"],
            s["started_at"],
            s["last_event_at"],
            s["input_tokens"],
            s["output_tokens"],
            s["cache_read_tokens"],
            s["cache_write_tokens"],
            s["cost_usd"],
            activity,
            one_shot,
            s["provider"],
        ),
    )

    # Synthetic aggregated message row
    msg_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s['provider']}:{s['session_id']}"))
    conn.execute(
        """INSERT OR IGNORE INTO messages
           (id, session_id, role, model, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens, cost_usd, created_at)
           VALUES (?,?,'assistant',?,?,?,?,?,?,?)""",
        (
            msg_id,
            s["session_id"],
            s["model"],
            s["input_tokens"],
            s["output_tokens"],
            s["cache_read_tokens"],
            s["cache_write_tokens"],
            s["cost_usd"],
            s["last_event_at"],
        ),
    )

    for tool_name in s["tools"]:
        conn.execute(
            """INSERT INTO tool_calls (id, session_id, message_id, tool_name, created_at)
               VALUES (?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                s["session_id"],
                msg_id,
                tool_name,
                s["last_event_at"],
            ),
        )

    log.debug("provider %s: ingested session %s", s["provider"], s["session_id"])
    return True


def sync_all_providers(conn: Connection) -> dict[str, int]:
    from .codex import discover as codex_discover
    from .gemini import discover as gemini_discover

    counts: dict[str, int] = {}
    for provider_name, discover_fn in [("codex", codex_discover), ("gemini", gemini_discover)]:
        new = 0
        try:
            sessions = discover_fn()
            for s in sessions:
                if sync_provider_session(conn, s):
                    new += 1
            if new:
                conn.commit()
                log.info("provider %s: +%d new sessions", provider_name, new)
        except Exception:
            log.exception("provider %s: sync failed", provider_name)
        counts[provider_name] = new
    return counts
