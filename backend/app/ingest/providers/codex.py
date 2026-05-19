"""Codex provider — walks ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import ProviderSession
from ..pricing import get_cost_usd

log = logging.getLogger(__name__)

CODEX_DIR = Path.home() / ".codex" / "sessions"

_TOOL_MAP = {
    "exec_command": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "list_directory": "LS",
    "search_files": "Grep",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
}


def _map_tool(name: str) -> str:
    return _TOOL_MAP.get(name, name)


def _parse_file(path: Path) -> ProviderSession | None:
    lines: list[dict] = []
    try:
        for raw in path.read_text(errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            lines.append(json.loads(raw))
    except Exception:
        return None

    if not lines:
        return None

    # First line must be session_meta from a codex originator
    meta = lines[0]
    if meta.get("type") != "session_meta":
        return None
    originator = meta.get("originator", "")
    if not str(originator).lower().startswith("codex"):
        return None

    session_id: str = meta.get("session_id") or path.stem
    project: str = meta.get("project") or path.parent.parent.parent.name
    cwd: str | None = meta.get("cwd")
    model: str = meta.get("model") or "codex"
    started_at: str = meta.get("created_at") or ""
    last_event_at: str = started_at

    input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    last_user_message: str | None = None
    tools: list[str] = []
    seen_tools: set[str] = set()

    for line in lines:
        t = line.get("type", "")
        ts = line.get("timestamp") or line.get("created_at") or ""
        if ts > last_event_at:
            last_event_at = ts

        if t == "token_count":
            usage = line.get("usage") or {}
            # Codex reports cumulative totals — take the latest snapshot
            input_tokens = usage.get("input_tokens", input_tokens)
            output_tokens = usage.get("output_tokens", output_tokens)
            cache_read_tokens = usage.get("cache_read_tokens", cache_read_tokens)
            cache_write_tokens = usage.get("cache_write_tokens", cache_write_tokens)

        elif t == "user":
            content = line.get("content")
            if isinstance(content, str) and content.strip():
                last_user_message = content.strip()
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        last_user_message = block.get("text", "").strip()

        elif t == "tool_use":
            name = _map_tool(line.get("name") or "")
            if name and name not in seen_tools:
                tools.append(name)
                seen_tools.add(name)

    cost_usd = get_cost_usd(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)

    return ProviderSession(
        session_id=session_id,
        project=project,
        cwd=cwd,
        model=model,
        started_at=started_at,
        last_event_at=last_event_at,
        last_user_message=last_user_message,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
        tools=tools,
        provider="codex",
        source_path=str(path),
    )


def discover() -> list[ProviderSession]:
    if not CODEX_DIR.exists():
        return []
    sessions: list[ProviderSession] = []
    for path in sorted(CODEX_DIR.rglob("rollout-*.jsonl")):
        try:
            s = _parse_file(path)
            if s:
                sessions.append(s)
        except Exception:
            log.debug("codex: skip %s", path)
    return sessions
