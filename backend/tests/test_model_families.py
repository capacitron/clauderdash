"""Regression tests for model-family classification.

Both cases here are defects that shipped and were caught in review, not
hypotheticals — keep them.
"""
from __future__ import annotations

import pytest

from app.api.panels import _classify_model
from app.config import MODEL_FAMILIES, MODEL_FAMILY_ORDER, PREMIUM_FAMILIES


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-fable-5", "fable"),
        ("claude-opus-5", "opus"),
        ("claude-sonnet-5", "sonnet"),
        ("claude-haiku-4-5-20251001", "haiku"),   # dated suffix
        ("claude-opus-5@20260201", "opus"),        # pinned
        ("anthropic/claude-opus-5", "opus"),       # provider-prefixed
        ("bedrock/claude-fable-5", "fable"),
        ("vertex_ai/claude-sonnet-5", "sonnet"),
        ("some-other-model", "unknown"),
        (None, "unknown"),
    ],
)
def test_classify_model_canonicalises(model, expected) -> None:
    """A proxy-routed id must land in the same family as the bare one.

    It previously did not: `bedrock/claude-opus-5` fell to "unknown" here while
    the drift rule's `model LIKE '%opus%'` matched it, so the gauge and the
    weekly rule disagreed about the same rows.
    """
    assert _classify_model(model) == expected


def test_family_order_covers_every_family() -> None:
    """/api/models builds its bucket dict from MODEL_FAMILY_ORDER but fills it
    using _classify_model, which draws from MODEL_FAMILIES. A family in one and
    not the other is a KeyError on a live endpoint."""
    assert set(MODEL_FAMILIES) | {"unknown"} == set(MODEL_FAMILY_ORDER)


def test_premium_families_are_real_families() -> None:
    assert set(PREMIUM_FAMILIES) <= set(MODEL_FAMILIES)


def test_model_mix_survives_an_unlisted_family(monkeypatch) -> None:
    """A family that classifies outside MODEL_FAMILY_ORDER must degrade, not 500.

    MODEL_FAMILY_ORDER is derived, so this state is unreachable through config
    alone — but /api/models used to index its bucket dicts directly, meaning any
    future desync would have been an unhandled KeyError on a live endpoint
    rather than one missing bar.
    """
    import app.api.panels as panels

    # Patch the classifier itself: the order list is derived from MODEL_FAMILIES
    # at import, so patching the dict alone cannot produce the desync.
    monkeypatch.setattr(panels, "_classify_model", lambda m: "ghost")
    out = panels.model_mix("7d")
    assert "ghost" not in MODEL_FAMILY_ORDER
    # Either no rows in the window, or the ghost bucket was created on demand.
    assert out["series"] == [] or "ghost" in out["totals"]
