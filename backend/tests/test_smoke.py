"""v2 + v3 smoke tests. DEV_MODE seeds fixtures so every endpoint has data."""
from __future__ import annotations

import pytest

# Isolation (temp DATA_DIR, DEV_MODE fixtures) is set up in conftest.py, which
# runs before this module is imported.
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_summary() -> None:
    r = client.get("/api/summary")
    assert r.status_code == 200
    data = r.json()
    assert "sessions" in data and "tool_calls" in data


def test_heatmap() -> None:
    r = client.get("/api/heatmap")
    assert r.status_code == 200
    data = r.json()
    assert data["weeks"] == 12
    assert isinstance(data["days"], list)
    # Fixture spread is 0..83 days → expect non-empty
    assert len(data["days"]) > 0


def test_status_cards() -> None:
    r = client.get("/api/status-cards")
    assert r.status_code == 200
    data = r.json()
    for k in ("live_sessions", "tokens_today", "avg_ctx_pct", "top_project"):
        assert k in data
    assert isinstance(data["avg_ctx_pct"], (int, float))
    assert 0.0 <= data["avg_ctx_pct"] <= 1.0


def test_status_cards_avg_ctx_pct_shape() -> None:
    """Characterization test: pins status_cards avg_ctx_pct behavior before _mc_avg_ctx_pct extraction."""
    r = client.get("/api/status-cards")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["avg_ctx_pct"], float)
    assert 0.0 <= data["avg_ctx_pct"] <= 1.0


def test_two_strike() -> None:
    r = client.get("/api/two-strike?range=30d")
    assert r.status_code == 200
    data = r.json()
    assert data["range"] == "30d"
    assert "fingerprints" in data
    # Fixture seeds 3 repeated errors
    assert len(data["fingerprints"]) >= 1


def test_revenue_lens() -> None:
    r = client.get("/api/revenue-lens?range=7d")
    assert r.status_code == 200
    data = r.json()
    assert "tags" in data and "untagged" in data
    assert "sessions" in data["untagged"] and "tokens" in data["untagged"]


def test_vault_ribbon() -> None:
    r = client.get("/api/vault-ribbon")
    assert r.status_code == 200
    data = r.json()
    for k in ("files", "scanned", "capped", "vault_exists"):
        assert k in data


def test_tools_has_optional_series() -> None:
    r = client.get("/api/tools?range=7d")
    assert r.status_code == 200
    data = r.json()
    assert "tools" in data
    # When tools are present, series field must be on each entry
    if data["tools"]:
        first = data["tools"][0]
        for k in ("tool", "calls", "p50_ms", "p95_ms", "max_ms", "series"):
            assert k in first, f"missing {k}"
        assert isinstance(first["series"], list)


def test_suggestions_empty() -> None:
    # File does not exist in fresh temp DATA_DIR → graceful empty response
    r = client.get("/api/suggestions")
    assert r.status_code == 200
    data = r.json()
    assert data["empty"] is True
    assert data["recommendations"] == []
    assert data["schema_version"] == 1
    assert data["generated_at"] is None


def test_suggestions_populated() -> None:
    import json
    from pathlib import Path
    from app.config import SUGGESTIONS_FILE

    fixture = {
        "schema_version": 1,
        "generated_at": "2026-04-27T15:00:00Z",
        "generator_model": "claude-haiku-4-5",
        "notes": None,
        "recommendations": [
            {
                "title": "Test recommendation",
                "body": "Test body citing 42 occurrences.",
                "evidence_sql": "SELECT 42",
                "confidence": 0.9,
                "tier": "high",
            }
        ],
    }
    Path(SUGGESTIONS_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(SUGGESTIONS_FILE).write_text(json.dumps(fixture))
    try:
        r = client.get("/api/suggestions")
        assert r.status_code == 200
        data = r.json()
        assert data["empty"] is False
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["title"] == "Test recommendation"
        assert data["generator_model"] == "claude-haiku-4-5"
    finally:
        Path(SUGGESTIONS_FILE).unlink(missing_ok=True)


# -------- v3 Mission Clock (pass_2_a) --------


@pytest.mark.parametrize("kwargs,expected_tier", [
    (dict(weekly_pct=0.92, five_hour_pct=0.50, opus_share_24h=0.45, burn_rate_ratio=1.0, avg_ctx_pct=0.10), 1),
    (dict(weekly_pct=0.50, five_hour_pct=0.91, opus_share_24h=0.20, burn_rate_ratio=1.0, avg_ctx_pct=0.10), 2),
    (dict(weekly_pct=0.85, five_hour_pct=0.50, opus_share_24h=0.30, burn_rate_ratio=1.0, avg_ctx_pct=0.10), 3),
    (dict(weekly_pct=0.50, five_hour_pct=0.75, opus_share_24h=0.20, burn_rate_ratio=1.0, avg_ctx_pct=0.10), 4),
    (dict(weekly_pct=0.20, five_hour_pct=0.10, opus_share_24h=0.10, burn_rate_ratio=3.5, avg_ctx_pct=0.10), 5),
    (dict(weekly_pct=0.20, five_hour_pct=0.10, opus_share_24h=0.10, burn_rate_ratio=1.0, avg_ctx_pct=0.42), 6),
    (dict(weekly_pct=0.20, five_hour_pct=0.10, opus_share_24h=0.10, burn_rate_ratio=1.0, avg_ctx_pct=0.10), 7),
])
def test_mission_clock_rules(kwargs, expected_tier):
    from app.api.panels import _mc_rules
    result = _mc_rules(**kwargs)
    assert result["tier"] == expected_tier
    assert result["color"] in ("red", "amber", "green")
    assert "directive" in result and "slash_cmd" in result


def test_mission_clock_endpoint() -> None:
    r = client.get("/api/mission-clock")
    assert r.status_code == 200
    data = r.json()
    for k in (
        "weekly_pct", "five_hour_pct", "burn_rate_ratio",
        "avg_ctx_pct", "opus_share_24h", "eta_seconds",
        "caps_configured", "action",
    ):
        assert k in data, f"missing key: {k}"
    action = data["action"]
    for k in ("tier", "color", "directive", "slash_cmd"):
        assert k in action
    assert action["color"] in ("red", "amber", "green")
    assert isinstance(data["burn_rate_ratio"], (int, float))
    assert data["burn_rate_ratio"] >= 0.0
