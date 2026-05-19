"""Gemini provider — walks ~/.gemini/tmp/<project>/chats/session-*.json[l]."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import ProviderSession
from ..pricing import get_cost_usd

log = logging.getLogger(__name__)

GEMINI_DIR = Path.home() / ".gemini" / "tmp"


def _parse_file(path: Path, project: str) -> ProviderSession | None:
    raw_text = path.read_text(errors="replace").strip()
    if not raw_text:
        return None

    # Support both single JSON object and JSONL (one JSON per line)
    records: list[dict] = []
    if raw_text.startswith("[") or raw_text.startswith("{"):
        try:
            obj = json.loads(raw_text)
            if isinstance(obj, list):
                records = obj
            elif isinstance(obj, dict):
                records = [obj]
        except json.JSONDecodeError:
            # Fall through to JSONL
            pass

    if not records:
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        return None

    # Derive session_id from filename (session-<uuid>.json[l])
    session_id = path.stem  # e.g. "session-abc123"
    if session_id.startswith("session-"):
        session_id = session_id[8:]

    model = "gemini"
    started_at = ""
    last_event_at = ""
    last_user_message: str | None = None
    input_tokens = output_tokens = 0
    tools: list[str] = []
    seen_tools: set[str] = set()
    cwd: str | None = None

    for rec in records:
        rec_type = rec.get("type", "")
        ts = rec.get("timestamp") or rec.get("created_at") or ""

        if ts:
            if not started_at:
                started_at = ts
            if ts > last_event_at:
                last_event_at = ts

        if rec_type == "session_start" or rec_type == "init":
            model = rec.get("model") or model
            cwd = rec.get("cwd") or cwd

        elif rec_type in ("human", "user"):
            content = rec.get("content") or rec.get("message") or ""
            if isinstance(content, str) and content.strip():
                last_user_message = content.strip()
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        last_user_message = block.get("text", "").strip()

        elif rec_type in ("assistant", "model"):
            usage = rec.get("usage_metadata") or rec.get("usage") or {}
            # Gemini uses prompt_token_count / candidates_token_count
            input_tokens += usage.get("prompt_token_count") or usage.get("input_tokens") or 0
            output_tokens += usage.get("candidates_token_count") or usage.get("output_tokens") or 0
            m = rec.get("model")
            if m:
                model = m

        elif rec_type == "tool_use":
            name = rec.get("name") or ""
            if name and name not in seen_tools:
                tools.append(name)
                seen_tools.add(name)

        elif rec_type == "function_call":
            name = (rec.get("function_call") or {}).get("name") or rec.get("name") or ""
            if name and name not in seen_tools:
                tools.append(name)
                seen_tools.add(name)

    if not started_at:
        # Use file mtime as fallback
        import datetime as dt
        mtime = path.stat().st_mtime
        started_at = dt.datetime.fromtimestamp(mtime, dt.UTC).isoformat()
        last_event_at = started_at

    cost_usd = get_cost_usd(model, input_tokens, output_tokens, 0, 0)

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
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=cost_usd,
        tools=tools,
        provider="gemini",
        source_path=str(path),
    )


def discover() -> list[ProviderSession]:
    if not GEMINI_DIR.exists():
        return []
    sessions: list[ProviderSession] = []
    for project_dir in GEMINI_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue
        project = project_dir.name
        for path in sorted(chats_dir.glob("session-*.json*")):
            try:
                s = _parse_file(path, project)
                if s:
                    sessions.append(s)
            except Exception:
                log.debug("gemini: skip %s", path)
    return sessions
