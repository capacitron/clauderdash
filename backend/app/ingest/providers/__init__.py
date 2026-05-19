from __future__ import annotations

from typing import TypedDict


class ProviderSession(TypedDict):
    session_id: str
    project: str
    cwd: str | None
    model: str
    started_at: str
    last_event_at: str
    last_user_message: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    tools: list[str]
    provider: str
    source_path: str
