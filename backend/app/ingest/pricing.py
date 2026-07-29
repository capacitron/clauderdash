"""LiteLLM-backed pricing. Sync lookup + async 24h refresh.

In-memory cache loaded at startup from disk cache or hardcoded fallback.
get_cost_usd() is always sync — safe to call from ingest hot path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from ..config import DATA_DIR

log = logging.getLogger(__name__)

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_CACHE_FILE = DATA_DIR / "pricing_cache.json"
_CACHE_TTL = 24 * 3600  # seconds

# Hardcoded fallback — per token (not per 1M). `ctx` = max input tokens.
# Cold-start only; the LiteLLM refresh overwrites this within a day.
_FALLBACK: dict[str, dict[str, float]] = {
    "claude-fable-5":    {"input": 10e-6, "output": 50e-6,  "cache_write": 12.5e-6,  "cache_read": 1e-6,     "ctx": 1_000_000},
    "claude-opus-5":     {"input": 5e-6,  "output": 25e-6,  "cache_write": 6.25e-6,  "cache_read": 0.5e-6,   "ctx": 1_000_000},
    "claude-opus-4-8":   {"input": 5e-6,  "output": 25e-6,  "cache_write": 6.25e-6,  "cache_read": 0.5e-6,   "ctx": 1_000_000},
    "claude-opus-4-7":   {"input": 15e-6, "output": 75e-6,  "cache_write": 18.75e-6, "cache_read": 1.5e-6,   "ctx": 1_000_000},
    "claude-opus-4-6":   {"input": 15e-6, "output": 75e-6,  "cache_write": 18.75e-6, "cache_read": 1.5e-6,   "ctx": 1_000_000},
    "claude-opus-4-5":   {"input": 15e-6, "output": 75e-6,  "cache_write": 18.75e-6, "cache_read": 1.5e-6,   "ctx": 200_000},
    "claude-sonnet-5":   {"input": 2e-6,  "output": 10e-6,  "cache_write": 2.5e-6,   "cache_read": 0.2e-6,   "ctx": 1_000_000},
    "claude-sonnet-4-6": {"input": 3e-6,  "output": 15e-6,  "cache_write": 3.75e-6,  "cache_read": 0.3e-6,   "ctx": 1_000_000},
    "claude-sonnet-4-5": {"input": 3e-6,  "output": 15e-6,  "cache_write": 3.75e-6,  "cache_read": 0.3e-6,   "ctx": 200_000},
    "claude-haiku-4-5":  {"input": 1e-6,  "output": 5e-6,   "cache_write": 1.25e-6,  "cache_read": 0.1e-6,   "ctx": 200_000},
}

DEFAULT_CONTEXT_WINDOW = 200_000

_cache: dict[str, dict[str, float]] = {}


def _canonical(model: str) -> str:
    """Strip @pin, date suffix (-YYYYMMDD), provider/ prefix."""
    model = model.split("@")[0]
    model = re.sub(r"-\d{8}$", "", model)
    model = re.sub(r"^[^/]+/", "", model)
    return model


def _lookup(model: str) -> dict[str, float]:
    canonical = _canonical(model)
    if canonical in _cache:
        return _cache[canonical]
    for key, val in _cache.items():
        if canonical.startswith(key) or key.startswith(canonical + "-"):
            return val
    for key, val in _FALLBACK.items():
        if canonical.startswith(key) or key.startswith(canonical + "-"):
            return val
    return _FALLBACK["claude-sonnet-5"]


def get_context_window(model: str | None) -> int:
    """Max input tokens for a model. LiteLLM-sourced, falls back to the table.

    Deliberately does NOT reuse _lookup: its catch-all returns a real model's
    entry for an unrecognised name, which is fine for a cost estimate but would
    claim a 1M window for a model we know nothing about. An unknown model gets
    the conservative default so the context heatmap under-reports rather than
    over-reports headroom. A disk cache written before `ctx` was parsed has no
    `ctx` key, so those misses fall through to _FALLBACK too.
    """
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    canonical = _canonical(model)
    for table in (_cache, _FALLBACK):
        for key, val in table.items():
            if "ctx" not in val:
                continue
            if canonical.startswith(key) or key.startswith(canonical + "-"):
                return int(val["ctx"])
    return DEFAULT_CONTEXT_WINDOW


def get_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    p = _lookup(model)
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write"]
    )


def _parse_litellm(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        inp = entry.get("input_cost_per_token")
        out = entry.get("output_cost_per_token")
        if inp is None or out is None:
            continue
        cw = entry.get("cache_creation_input_token_cost", inp * 1.25)
        cr = entry.get("cache_read_input_token_cost", inp * 0.1)
        parsed = {"input": float(inp), "output": float(out), "cache_write": float(cw), "cache_read": float(cr)}
        ctx = entry.get("max_input_tokens")
        if ctx:
            parsed["ctx"] = int(ctx)
        result[name] = parsed
        stripped = re.sub(r"^[^/]+/", "", name)
        if stripped != name and stripped not in result:
            result[stripped] = parsed
    return result


def _fetch_litellm() -> dict[str, dict[str, float]] | None:
    try:
        req = urllib.request.Request(
            LITELLM_URL, headers={"User-Agent": "claude-dashboard/2.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
        return _parse_litellm(raw)
    except Exception as exc:
        log.warning("pricing fetch failed: %s", exc)
        return None


def load_pricing_sync() -> None:
    """Load pricing from disk cache or fallback. Call once at startup."""
    global _cache
    try:
        if _CACHE_FILE.exists():
            payload = json.loads(_CACHE_FILE.read_text())
            age = time.time() - float(payload.get("ts", 0))
            if age < _CACHE_TTL and payload.get("data"):
                _cache = payload["data"]
                log.info("pricing loaded from cache (age %.0fh)", age / 3600)
                return
    except Exception as exc:
        log.warning("pricing cache read failed: %s", exc)
    _cache = dict(_FALLBACK)
    log.info("pricing using hardcoded fallback")


async def run_pricing_refresh() -> None:
    """Background task: load once, then refresh every 24h."""
    global _cache
    loop = asyncio.get_event_loop()
    load_pricing_sync()

    # Immediate first fetch if cache is cold
    if not _cache or _cache is _FALLBACK:
        data = await loop.run_in_executor(None, _fetch_litellm)
        if data:
            _cache = data
            _save_cache(data)

    while True:
        await asyncio.sleep(_CACHE_TTL)
        data = await loop.run_in_executor(None, _fetch_litellm)
        if data:
            _cache = data
            _save_cache(data)
            log.info("pricing refreshed from LiteLLM (%d models)", len(data))


def _save_cache(data: dict) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception as exc:
        log.warning("pricing cache write failed: %s", exc)
