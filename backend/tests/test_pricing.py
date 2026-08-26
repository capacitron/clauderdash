"""Fault isolation in the LiteLLM price parser.

Upstream ships a ~4,000-model JSON blob. A single malformed entry used to abort
the whole parse: the exception propagated out of _parse_litellm into
_fetch_litellm's blanket `except Exception`, which discarded an otherwise
successful fetch and left the server on hardcoded fallback prices. That went
unnoticed for 29 days because the fallback happened to match the live models.
"""
from __future__ import annotations

from app.ingest import pricing

# Verbatim from the production failure. LiteLLM ships this documentation string
# in `max_input_tokens` for at least one model:
#   invalid literal for int() with base 10: 'max input tokens, if the provider
#   specifies it. if not default to max_tokens'
PROSE_IN_A_NUMERIC_FIELD = (
    "max input tokens, if the provider specifies it. if not default to max_tokens"
)


def _good(name: str = "claude-sonnet-5") -> dict:
    return {
        name: {
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 10e-6,
            "cache_creation_input_token_cost": 2.5e-6,
            "cache_read_input_token_cost": 0.2e-6,
            "max_input_tokens": 1_000_000,
        }
    }


def test_malformed_ctx_does_not_lose_sibling_entries():
    """The bug: one bad entry took every other model down with it."""
    data = {**_good(), "sample_spec": {
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 5e-6,
        "max_input_tokens": PROSE_IN_A_NUMERIC_FIELD,
    }}

    result = pricing._parse_litellm(data)

    assert "claude-sonnet-5" in result
    assert result["claude-sonnet-5"]["input"] == 2e-6
    assert result["claude-sonnet-5"]["ctx"] == 1_000_000


def test_malformed_ctx_keeps_its_own_prices():
    """`ctx` is optional metadata. A bad value costs the entry its context
    window, never its prices — those are what the cost hot path needs."""
    result = pricing._parse_litellm({"sample_spec": {
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 5e-6,
        "max_input_tokens": PROSE_IN_A_NUMERIC_FIELD,
    }})

    assert result["sample_spec"]["input"] == 1e-6
    assert result["sample_spec"]["output"] == 5e-6
    assert "ctx" not in result["sample_spec"]


def test_unparseable_price_skips_only_that_entry():
    """A malformed price field is unrecoverable for that model, but must not
    take the rest of the batch with it."""
    data = {**_good(), "broken": {
        "input_cost_per_token": "free!",
        "output_cost_per_token": 5e-6,
    }}

    result = pricing._parse_litellm(data)

    assert "broken" not in result
    assert "claude-sonnet-5" in result


def test_provider_prefix_is_still_aliased():
    """Characterization: existing behaviour, unchanged by the fault isolation."""
    result = pricing._parse_litellm({"anthropic/claude-opus-5": {
        "input_cost_per_token": 5e-6,
        "output_cost_per_token": 25e-6,
        "max_input_tokens": 1_000_000,
    }})

    assert "anthropic/claude-opus-5" in result
    assert "claude-opus-5" in result
    assert result["claude-opus-5"] is result["anthropic/claude-opus-5"]


def test_entry_without_prices_is_skipped():
    """Characterization: entries missing either price were already skipped."""
    result = pricing._parse_litellm({"no_prices": {"max_input_tokens": 200_000}})

    assert result == {}
