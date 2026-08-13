"""Billing-mode detection: are the dollars on this dashboard an invoice or an estimate?

Every cost figure in this app is derived from token counts times a LiteLLM price
table. Anthropic never writes a cost into the JSONL. On a subscription those
dollars are an API-equivalent estimate and nobody is billing them; on an API key
they approximate a real invoice. Same number, opposite meaning, so the UI has to
say which one it is.

Read-only. Touches ~/.claude/.credentials.json, ~/.claude.json, and the env.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path.home() / ".claude"
CREDENTIALS_FILE = CLAUDE_DIR / ".credentials.json"
CONFIG_FILE = Path.home() / ".claude.json"

# Claude Code identifies a custom API key by its last 20 characters and records
# the user's approve/reject decision under customApiKeyResponses. A key sitting
# in the environment that was never approved is NOT the one being billed.
_KEY_ID_LEN = 20

_CACHE: dict[str, Any] = {"payload": None, "ts": 0.0}
_CACHE_TTL = 300.0  # seconds; auth mode changes at login, not mid-session


def key_id(api_key: str) -> str:
    """Claude Code's identifier for a custom API key: its last 20 chars."""
    return api_key[-_KEY_ID_LEN:]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _oauth_block(creds: dict[str, Any]) -> dict[str, Any]:
    """claudeAiOauth if present and unexpired, else {}."""
    oauth = creds.get("claudeAiOauth")
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return {}
    # expiresAt is epoch millis. An expired access token still proves the account
    # is a subscription one, so treat expiry as a staleness note, not a rejection.
    return oauth


def detect_billing_mode(
    env: dict[str, str] | None = None,
    creds: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve how this machine pays for tokens.

    Args are injectable for tests; production passes nothing and reads real files.

    mode:
      subscription  - Pro/Max plan via OAuth. Costs are estimates, not billed.
      api           - Approved custom API key. Costs approximate a real invoice.
      third_party   - Custom auth token or non-Anthropic base URL (proxy, Bedrock,
                      Vertex, OpenRouter). Anthropic list prices may not apply at all.
      unknown       - No usable signal.
    """
    env = os.environ if env is None else env
    creds = _read_json(CREDENTIALS_FILE) if creds is None else creds
    config = _read_json(CONFIG_FILE) if config is None else config

    auth_token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    base_url = env.get("ANTHROPIC_BASE_URL") or ""
    api_key = env.get("ANTHROPIC_API_KEY") or ""

    oauth = _oauth_block(creds)
    account = config.get("oauthAccount") if isinstance(config.get("oauthAccount"), dict) else {}
    responses = config.get("customApiKeyResponses")
    responses = responses if isinstance(responses, dict) else {}
    approved = set(responses.get("approved") or [])
    rejected = set(responses.get("rejected") or [])

    result: dict[str, Any] = {
        "mode": "unknown",
        "cost_is_billed": None,
        "reason": "No auth signal found",
        "plan": None,
        "rate_limit_tier": None,
        "billing_type": None,
        "organization_type": None,
        "extra_usage_enabled": None,
        "api_key_present": bool(api_key),
        "api_key_approved": bool(api_key) and key_id(api_key) in approved,
        "api_key_rejected": bool(api_key) and key_id(api_key) in rejected,
        "detected_at": time.time(),
    }

    # A gateway overrides everything below it: whoever runs the endpoint sets the price.
    if auth_token or (base_url and "api.anthropic.com" not in base_url):
        result.update(
            mode="third_party",
            cost_is_billed=None,
            reason=(
                "ANTHROPIC_AUTH_TOKEN is set"
                if auth_token
                else f"ANTHROPIC_BASE_URL points at {base_url}"
            ),
        )
        return result

    if api_key and result["api_key_approved"]:
        result.update(
            mode="api",
            cost_is_billed=True,
            reason="Custom API key present and approved in customApiKeyResponses",
        )
        return result

    if oauth:
        plan = oauth.get("subscriptionType") or account.get("organizationType")
        extra = account.get("hasExtraUsageEnabled")
        reason = f"OAuth subscription ({plan or 'unknown plan'})"
        if result["api_key_rejected"]:
            reason += "; an API key is in the environment but was rejected, so it is not in use"
        elif api_key:
            reason += "; an API key is in the environment but was never approved"
        result.update(
            mode="subscription",
            # Even with extra usage on, only overage past the plan allowance bills.
            # Never claim these totals are an invoice.
            cost_is_billed=False,
            reason=reason,
            plan=plan,
            rate_limit_tier=(
                oauth.get("rateLimitTier") or account.get("organizationRateLimitTier")
            ),
            billing_type=account.get("billingType"),
            organization_type=account.get("organizationType"),
            extra_usage_enabled=extra,
        )
        return result

    if api_key:
        result.update(
            mode="api",
            cost_is_billed=True,
            reason="API key in environment with no OAuth subscription present",
        )
        return result

    return result


def get_billing_mode(force: bool = False) -> dict[str, Any]:
    """Cached detect_billing_mode(). Safe to call from a request handler."""
    now = time.time()
    if not force and _CACHE["payload"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["payload"]
    payload = detect_billing_mode()
    _CACHE["payload"] = payload
    _CACHE["ts"] = now
    return payload


def cost_label(mode: str) -> dict[str, str]:
    """UI copy for a cost figure under this billing mode."""
    return {
        "subscription": {
            "noun": "estimated cost",
            "qualifier": "API-equivalent, not billed",
            "tooltip": (
                "You are on a subscription. These dollars are token counts priced at "
                "Anthropic's public API rates. They are a workload measure, not an invoice."
            ),
        },
        "api": {
            "noun": "cost",
            "qualifier": "billed to your API account",
            "tooltip": (
                "Derived from token counts at Anthropic's public API rates. Close to your "
                "invoice, but excludes discounts, batch pricing, and credits."
            ),
        },
        "third_party": {
            "noun": "estimated cost",
            "qualifier": "gateway pricing unknown",
            "tooltip": (
                "Traffic is routed through a custom endpoint. Anthropic's list prices are "
                "used as a stand-in and may not reflect what you actually pay."
            ),
        },
        "unknown": {
            "noun": "estimated cost",
            "qualifier": "billing mode undetected",
            "tooltip": "Could not determine how this machine pays for tokens.",
        },
    }.get(mode, {"noun": "estimated cost", "qualifier": "", "tooltip": ""})


def demo() -> None:
    """Self-check. Run: python -m app.ingest.billing"""
    sub_creds = {"claudeAiOauth": {"accessToken": "x", "subscriptionType": "max",
                                   "rateLimitTier": "default_claude_max_5x"}}
    sub_cfg = {"oauthAccount": {"billingType": "stripe_subscription",
                                "organizationType": "claude_max",
                                "hasExtraUsageEnabled": False},
               "customApiKeyResponses": {"approved": [], "rejected": ["Z" * 20]}}

    # Subscription wins over an unapproved key sitting in the environment.
    r = detect_billing_mode({"ANTHROPIC_API_KEY": "sk-ant-" + "Z" * 20}, sub_creds, sub_cfg)
    assert r["mode"] == "subscription", r
    assert r["cost_is_billed"] is False
    assert r["plan"] == "max"
    assert r["api_key_rejected"] is True
    assert "rejected" in r["reason"]

    # An approved key beats the subscription.
    cfg = {**sub_cfg, "customApiKeyResponses": {"approved": ["Z" * 20], "rejected": []}}
    r = detect_billing_mode({"ANTHROPIC_API_KEY": "sk-ant-" + "Z" * 20}, sub_creds, cfg)
    assert r["mode"] == "api" and r["cost_is_billed"] is True, r

    # A gateway beats everything.
    r = detect_billing_mode({"ANTHROPIC_BASE_URL": "https://proxy.internal/v1"}, sub_creds, sub_cfg)
    assert r["mode"] == "third_party" and r["cost_is_billed"] is None, r
    r = detect_billing_mode({"ANTHROPIC_AUTH_TOKEN": "tok"}, sub_creds, sub_cfg)
    assert r["mode"] == "third_party", r

    # Anthropic's own base URL is not a gateway.
    r = detect_billing_mode({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, sub_creds, sub_cfg)
    assert r["mode"] == "subscription", r

    # Bare API key, no subscription.
    r = detect_billing_mode({"ANTHROPIC_API_KEY": "sk-ant-" + "Q" * 20}, {}, {})
    assert r["mode"] == "api" and r["cost_is_billed"] is True, r

    # Nothing at all.
    assert detect_billing_mode({}, {}, {})["mode"] == "unknown"

    # An OAuth block with no token is not a subscription.
    assert detect_billing_mode({}, {"claudeAiOauth": {}}, {})["mode"] == "unknown"

    assert key_id("sk-ant-api03-abcdefghijklmnopqrstuvwxyz") == "ghijklmnopqrstuvwxyz"

    print("billing self-check OK")
    print(json.dumps(detect_billing_mode(), indent=2, default=str))


if __name__ == "__main__":
    demo()
