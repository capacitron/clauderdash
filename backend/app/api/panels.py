"""All panel endpoints in one place. v1 (10) + v2 (5 new + /api/tools extension).

Each endpoint: JSON only, no templating. Frontend owns rendering.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from ..config import (
    HEATMAP_WEEKS,
    HOME,
    OBSIDIAN_DIR,
    OBSIDIAN_MAX_SCAN_FILES,
    LIVE_WINDOW_SECONDS,
    MODEL_FAMILIES,
    MODEL_FAMILY_ORDER,
    PREMIUM_COST_SHARE_TARGET,
    PREMIUM_FAMILIES,
    PROJECT_TAG_RULES,
    SUGGESTIONS_FILE,
)
from ..db import readonly_cursor
from ..ingest.pricing import canonical_model, get_context_window, get_cost_usd
from .ranges import percentile, resolve_range

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- helpers --
def _provider_clause(provider: str | None, alias: str = "s") -> tuple[str, list]:
    """Return (sql_fragment, params) for optional provider filter on sessions table."""
    if provider and provider != "all":
        return f" AND {alias}.provider = ?", [provider]
    return "", []


def _bucket_day(iso_ts: str | None) -> str | None:
    if not iso_ts:
        return None
    return iso_ts[:10]


def _classify_model(model: str | None) -> str:
    """Family for a model id, canonicalised first.

    Without canonicalisation a proxy-routed id like `bedrock/claude-opus-5`
    falls to "unknown" here, while the drift rule's `model LIKE '%opus%'`
    matches it — so the gauge and the weekly rule would disagree about the
    same rows.
    """
    if not model:
        return "unknown"
    lower = canonical_model(model.lower())
    for family, prefixes in MODEL_FAMILIES.items():
        if any(lower.startswith(p) for p in prefixes):
            return family
    return "unknown"


def _context_window_for(model: str | None) -> int:
    return get_context_window(model)


# ------------------------------------------------------------- summary --
@router.get("/summary")
def summary() -> dict[str, Any]:
    today_prefix = _utc_today_prefix()
    with readonly_cursor() as cur:
        total_sessions = cur.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        total_messages = cur.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        total_tools = cur.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"]
        total_otel = cur.execute("SELECT COUNT(*) AS n FROM otel_events").fetchone()["n"]
        last_otel_row = cur.execute(
            "SELECT received_at FROM otel_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_session_row = cur.execute(
            "SELECT last_modified_ts FROM sessions ORDER BY last_modified_ts DESC LIMIT 1"
        ).fetchone()
        cost_today_row = cur.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM messages WHERE substr(ts,1,10) = ?",
            (today_prefix,),
        ).fetchone()
        cost_total_row = cur.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM messages"
        ).fetchone()
    return {
        "sessions": total_sessions,
        "messages": total_messages,
        "tool_calls": total_tools,
        "otel_events": total_otel,
        "last_otel_received_at": last_otel_row["received_at"] if last_otel_row else None,
        "last_session_modified_ts": (
            last_session_row["last_modified_ts"] if last_session_row else None
        ),
        "cost_today_usd": float(cost_today_row["c"] or 0),
        "cost_total_usd": float(cost_total_row["c"] or 0),
        "now_ts": time.time(),
    }


# ----------------------------------------------------- 1. live sessions --
@router.get("/live")
def live_sessions() -> dict[str, Any]:
    cutoff = time.time() - LIVE_WINDOW_SECONDS
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT id, cwd, model, started_at, last_event_at, last_user_message,
                   last_modified_ts, entrypoint
              FROM sessions
             WHERE last_modified_ts >= ?
          ORDER BY last_modified_ts DESC
             LIMIT 50
            """,
            (cutoff,),
        ).fetchall()
        sessions: list[dict[str, Any]] = []
        for r in rows:
            token_row = cur.execute(
                """
                SELECT COALESCE(SUM(input_tokens),0) AS i,
                       COALESCE(SUM(output_tokens),0) AS o,
                       COALESCE(SUM(cache_read_tokens),0) AS cr,
                       COALESCE(SUM(cache_create_tokens),0) AS cc
                  FROM messages
                 WHERE session_id = ?
                """,
                (r["id"],),
            ).fetchone()
            total_tokens = (
                (token_row["i"] or 0)
                + (token_row["o"] or 0)
                + (token_row["cr"] or 0)
                + (token_row["cc"] or 0)
            )
            sessions.append(
                {
                    "id": r["id"],
                    "cwd": r["cwd"],
                    "model": r["model"],
                    "started_at": r["started_at"],
                    "last_event_at": r["last_event_at"],
                    "last_user_message": r["last_user_message"],
                    "total_tokens": total_tokens,
                    "entrypoint": r["entrypoint"],
                }
            )
    return {"sessions": sessions, "cutoff_seconds": LIVE_WINDOW_SECONDS}


@router.get("/session/{session_id}")
def session_detail(session_id: str) -> dict[str, Any]:
    with readonly_cursor() as cur:
        session = cur.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"error": "not_found"}
        tools = cur.execute(
            """
            SELECT tool_use_id, tool_name, is_mcp, mcp_server, mcp_tool,
                   started_at, ended_at, duration_ms, is_error, input_preview, error_preview
              FROM tool_calls
             WHERE session_id = ?
          ORDER BY started_at ASC
             LIMIT 500
            """,
            (session_id,),
        ).fetchall()
        usage_row = cur.execute(
            """
            SELECT COALESCE(SUM(input_tokens),0) AS i,
                   COALESCE(SUM(output_tokens),0) AS o,
                   COALESCE(SUM(cache_read_tokens),0) AS cr,
                   COALESCE(SUM(cache_create_tokens),0) AS cc,
                   COUNT(*) AS n_messages
              FROM messages WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    return {
        "session": dict(session),
        "usage": dict(usage_row),
        "tools": [dict(t) for t in tools],
    }


# ------------------------------------------------------- 2. token usage --
@router.get("/tokens")
def token_usage(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT substr(ts, 1, 10) AS day,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                   COALESCE(SUM(cache_create_tokens),0) AS cache_create_tokens
              FROM messages
             WHERE ts >= ?
          GROUP BY day
          ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
    series = [dict(r) for r in rows]
    totals = {
        k: sum(int(r[k] or 0) for r in series)
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens")
    }
    totals["grand_total"] = sum(totals.values())
    return {"range": range, "days": days, "series": series, "totals": totals}


# ------------------------------------------------------ 3. tool latency --
@router.get("/tools")
def tool_latency(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT tool_name, duration_ms, is_error
              FROM tool_calls
             WHERE started_at >= ?
               AND duration_ms IS NOT NULL
            """,
            (since,),
        ).fetchall()
        # v2: additive per-tool daily call-count series over the same range
        series_rows = cur.execute(
            """
            SELECT tool_name,
                   substr(started_at, 1, 10) AS day,
                   COUNT(*) AS calls
              FROM tool_calls
             WHERE started_at >= ?
               AND tool_name IS NOT NULL
          GROUP BY tool_name, day
          ORDER BY tool_name, day ASC
            """,
            (since,),
        ).fetchall()
    series_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sr in series_rows:
        series_map[sr["tool_name"]].append({"day": sr["day"], "calls": int(sr["calls"])})
    buckets: dict[str, list[int]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        name = r["tool_name"] or "?"
        buckets[name].append(int(r["duration_ms"]))
        totals[name] += 1
        if r["is_error"]:
            errors[name] += 1
    result = []
    for name, durations in buckets.items():
        durations.sort()
        result.append(
            {
                "tool": name,
                "calls": totals[name],
                "errors": errors[name],
                "error_rate": (errors[name] / totals[name]) if totals[name] else 0,
                "p50_ms": round(percentile(durations, 0.5)),
                "p95_ms": round(percentile(durations, 0.95)),
                "max_ms": durations[-1],
                "series": series_map.get(name, []),
            }
        )
    result.sort(key=lambda r: r["p95_ms"], reverse=True)
    return {"range": range, "days": days, "tools": result}


# -------------------------------------------- 4. MCP drill-down (star) --
@router.get("/mcp")
def mcp_drill(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT mcp_server, mcp_tool, duration_ms, is_error
              FROM tool_calls
             WHERE is_mcp = 1
               AND started_at >= ?
               AND duration_ms IS NOT NULL
            """,
            (since,),
        ).fetchall()
    by_server: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_server[r["mcp_server"] or "?"].append(dict(r))

    servers = []
    for server, items in by_server.items():
        durations = sorted(int(i["duration_ms"]) for i in items)
        errors = sum(1 for i in items if i["is_error"])
        # per-tool breakdown
        tool_bucket: dict[str, list[int]] = defaultdict(list)
        tool_errors: dict[str, int] = defaultdict(int)
        for i in items:
            tname = i["mcp_tool"] or "?"
            tool_bucket[tname].append(int(i["duration_ms"]))
            if i["is_error"]:
                tool_errors[tname] += 1
        tools_out = []
        for tname, durs in tool_bucket.items():
            durs.sort()
            calls = len(durs)
            tools_out.append(
                {
                    "tool": tname,
                    "calls": calls,
                    "errors": tool_errors[tname],
                    "error_rate": tool_errors[tname] / calls if calls else 0,
                    "p50_ms": round(percentile(durs, 0.5)),
                    "p95_ms": round(percentile(durs, 0.95)),
                    "max_ms": durs[-1],
                }
            )
        tools_out.sort(key=lambda t: t["p95_ms"], reverse=True)
        calls = len(items)
        servers.append(
            {
                "server": server,
                "calls": calls,
                "errors": errors,
                "error_rate": errors / calls if calls else 0,
                "avg_ms": round(sum(durations) / calls) if calls else 0,
                "p50_ms": round(percentile(durations, 0.5)),
                "p95_ms": round(percentile(durations, 0.95)),
                "max_ms": durations[-1] if durations else 0,
                "tools": tools_out,
            }
        )
    servers.sort(key=lambda s: s["p95_ms"], reverse=True)
    return {"range": range, "days": days, "servers": servers}


# -------------------------------------------------- 5. session outcomes --
@router.get("/outcomes")
def session_outcomes(range: str = Query("7d"), provider: str = Query("all")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    pclause, pparams = _provider_clause(provider, alias="sessions")
    with readonly_cursor() as cur:
        rows = cur.execute(
            f"""
            SELECT substr(started_at,1,10) AS day, outcome, COUNT(*) AS n
              FROM sessions
             WHERE started_at >= ?{pclause}
          GROUP BY day, outcome
          ORDER BY day ASC
            """,
            [since] + pparams,
        ).fetchall()
    by_day: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        by_day[r["day"]][r["outcome"] or "unfinished"] = int(r["n"])
    series = []
    for day in sorted(by_day.keys()):
        counts = by_day[day]
        series.append(
            {
                "day": day,
                "errored": counts.get("errored", 0),
                "rate_limited": counts.get("rate_limited", 0),
                "truncated": counts.get("truncated", 0),
                "unfinished": counts.get("unfinished", 0),
                "ok": counts.get("ok", 0),
            }
        )
    return {"range": range, "days": days, "series": series}


# ----------------------------------------------- 6. cache efficiency ---
@router.get("/cache")
def cache_efficiency(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT substr(ts,1,10) AS day,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                   COALESCE(SUM(cache_create_tokens),0) AS cache_create
              FROM messages
             WHERE ts >= ?
          GROUP BY day
          ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
    series = []
    total_input = total_read = total_create = 0
    for r in rows:
        inp = int(r["input_tokens"] or 0)
        cr = int(r["cache_read"] or 0)
        cc = int(r["cache_create"] or 0)
        billable = inp + cr + cc
        hit_rate = (cr / billable) if billable else 0.0
        series.append(
            {
                "day": r["day"],
                "billable_tokens": billable,
                "cache_read": cr,
                "hit_rate": hit_rate,
                "low_sample": billable < 10_000,
            }
        )
        total_input += inp
        total_read += cr
        total_create += cc
    total_billable = total_input + total_read + total_create
    overall = (total_read / total_billable) if total_billable else 0
    return {
        "range": range,
        "days": days,
        "overall_hit_rate": overall,
        "target": 0.70,
        "total_billable_tokens": total_billable,
        "low_sample": total_billable < 10_000,
        "series": series,
    }


# ----------------------------------------------------- 7. skills panel --
@router.get("/skills")
def skills(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        inv_rows = cur.execute(
            """
            SELECT skill_name, session_id, ts
              FROM skill_invocations
             WHERE ts >= ?
            """,
            (since,),
        ).fetchall()
        # Aggregate tokens per session for attribution
        token_rows = cur.execute(
            """
            SELECT session_id,
                   COALESCE(SUM(input_tokens),0) AS i,
                   COALESCE(SUM(output_tokens),0) AS o,
                   COALESCE(SUM(cache_read_tokens),0) AS cr,
                   COALESCE(SUM(cache_create_tokens),0) AS cc
              FROM messages
             WHERE ts >= ?
          GROUP BY session_id
            """,
            (since,),
        ).fetchall()
    session_tokens = {
        r["session_id"]: int(r["i"] or 0) + int(r["o"] or 0)
        + int(r["cr"] or 0) + int(r["cc"] or 0)
        for r in token_rows
    }
    by_skill: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"activations": 0, "total_tokens": 0, "sessions": set()}
    )
    for r in inv_rows:
        name = r["skill_name"]
        bucket = by_skill[name]
        bucket["activations"] += 1
        bucket["sessions"].add(r["session_id"])
    for name, bucket in by_skill.items():
        bucket["total_tokens"] = sum(
            session_tokens.get(sid, 0) for sid in bucket["sessions"]
        )
        bucket["sessions_count"] = len(bucket["sessions"])
        bucket["cost_per_run"] = (
            bucket["total_tokens"] / bucket["activations"]
            if bucket["activations"] else 0
        )
        del bucket["sessions"]
    out = [{"skill": name, **data} for name, data in by_skill.items()]
    out.sort(key=lambda s: s["total_tokens"], reverse=True)
    return {"range": range, "days": days, "skills": out}


# --------------------------------------------------- 7b. subagent usage --
@router.get("/agent-usage")
def agent_usage(range: str = Query("7d")) -> dict[str, Any]:
    """Cost/token attribution by subagent type.

    Direct per-message attribution, unlike /skills which infers cost by
    attributing a whole session's tokens to any skill invoked in it — every
    row here already carries its own agent_type (stamped at ingest from the
    subagent transcript's `.meta.json` sidecar), so this is exact, not a
    session-level heuristic.
    """
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT agent_type,
                   COUNT(*) AS runs,
                   COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_create_tokens),0) AS tokens,
                   COALESCE(SUM(cost_usd),0) AS cost
              FROM messages
             WHERE role = 'assistant' AND ts >= ? AND agent_type IS NOT NULL
          GROUP BY agent_type
          ORDER BY cost DESC
            """,
            (since,),
        ).fetchall()
        total_row = cur.execute(
            """
            SELECT COALESCE(SUM(cost_usd),0) AS cost
              FROM messages
             WHERE role = 'assistant' AND ts >= ?
            """,
            (since,),
        ).fetchone()

    total_cost = float(total_row["cost"] or 0)
    agent_cost = sum(float(r["cost"] or 0) for r in rows)
    by_agent = [
        {
            "agent_type": r["agent_type"],
            "runs": int(r["runs"]),
            "tokens": int(r["tokens"] or 0),
            "cost_usd": round(float(r["cost"] or 0), 4),
            "cost_share": (float(r["cost"] or 0) / total_cost) if total_cost else 0.0,
        }
        for r in rows
    ]
    return {
        "range": range,
        "days": days,
        "agents": by_agent,
        "total_cost_usd": round(total_cost, 4),
        "agent_cost_usd": round(agent_cost, 4),
        # Share of ALL spend (main-loop + subagents) that ran through a
        # subagent — the direct-attribution version of /usage's "76% of your
        # usage came from subagent-heavy sessions".
        "agent_cost_share": (agent_cost / total_cost) if total_cost else 0.0,
    }


# -------------------------------------------------- 8. model mix (Opus%) --
@router.get("/models")
def model_mix(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT substr(ts,1,10) AS day, model,
                   COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_create_tokens),0) AS tokens,
                   COALESCE(SUM(cost_usd), 0) AS cost,
                   COUNT(*) AS n
              FROM messages
             WHERE ts >= ? AND role='assistant'
          GROUP BY day, model
          ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
    # Buckets are derived from MODEL_FAMILY_ORDER, not spelled out — a new
    # family in config used to silently land in `unknown` (Fable did).
    def _empty() -> dict[str, Any]:
        d: dict[str, Any] = {}
        for fam in MODEL_FAMILY_ORDER:
            d[fam] = 0
            d[f"{fam}_cost"] = 0.0
        return d

    by_day: dict[str, dict[str, Any]] = defaultdict(_empty)
    totals: dict[str, Any] = _empty()
    for r in rows:
        family = _classify_model(r["model"])
        tokens = int(r["tokens"] or 0)
        cost = float(r["cost"] or 0)
        day = by_day[r["day"]]
        # .get(): MODEL_FAMILY_ORDER is derived from MODEL_FAMILIES so the keys
        # should always exist, but a config edit that breaks that should drop a
        # family off the chart, not 500 the endpoint.
        day[family] = day.get(family, 0) + tokens
        day[f"{family}_cost"] = day.get(f"{family}_cost", 0.0) + cost
        totals[family] = totals.get(family, 0) + tokens
        totals[f"{family}_cost"] = totals.get(f"{family}_cost", 0.0) + cost

    series = []
    for day in sorted(by_day.keys()):
        d = by_day[day]
        total = sum(d[f] for f in MODEL_FAMILY_ORDER) or 1
        series.append(
            {
                "day": day,
                **d,
                **{f"{fam}_pct": d[fam] / total for fam in MODEL_FAMILY_ORDER},
            }
        )
    grand_total = sum(totals[f] for f in MODEL_FAMILY_ORDER) or 1
    total_cost = sum(totals[f"{f}_cost"] for f in MODEL_FAMILY_ORDER)
    premium_cost = sum(totals[f"{f}_cost"] for f in PREMIUM_FAMILIES)
    return {
        "range": range,
        "days": days,
        "families": MODEL_FAMILY_ORDER,
        "premium_families": list(PREMIUM_FAMILIES),
        "series": series,
        "totals": totals,
        "total_cost_usd": total_cost,
        "premium_cost_usd": premium_cost,
        "premium_cost_share": premium_cost / total_cost if total_cost else 0.0,
        "premium_target_max": PREMIUM_COST_SHARE_TARGET,
        # Token share per family, kept for the mix chart's percentage labels.
        **{f"{fam}_pct_overall": totals[fam] / grand_total for fam in MODEL_FAMILY_ORDER},
    }


# --------------------------------------- 9. project activity (by cwd) --
@router.get("/projects")
def project_activity(range: str = Query("7d"), provider: str = Query("all")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    pclause, pparams = _provider_clause(provider, alias="s")
    with readonly_cursor() as cur:
        rows = cur.execute(
            f"""
            SELECT s.cwd AS cwd,
                   COUNT(DISTINCT s.id) AS sessions,
                   COALESCE(SUM(m.input_tokens+m.output_tokens+m.cache_read_tokens+m.cache_create_tokens),0) AS tokens,
                   COALESCE(SUM(m.cost_usd), 0) AS cost,
                   MAX(s.last_modified_ts) AS last_active
              FROM sessions s
              LEFT JOIN messages m ON m.session_id = s.id
             WHERE s.started_at >= ?{pclause}
          GROUP BY s.cwd
            """,
            [since] + pparams,
        ).fetchall()
    projects = [
        {
            "cwd": r["cwd"] or "(unknown)",
            "sessions": r["sessions"],
            "tokens": int(r["tokens"] or 0),
            "cost_usd": float(r["cost"] or 0),
            "last_active": r["last_active"],
        }
        for r in rows
    ]
    projects.sort(key=lambda p: p["tokens"], reverse=True)
    return {"range": range, "days": days, "projects": projects}


# ------------------------------------------- activities (classifier) --
@router.get("/activities")
def activities(range: str = Query("7d"), provider: str = Query("all")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    pclause, pparams = _provider_clause(provider, alias="sessions")
    with readonly_cursor() as cur:
        rows = cur.execute(
            f"""
            SELECT activity_type,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(cost_usd), 0) AS cost,
                   COALESCE(SUM(one_shot), 0) AS one_shots
              FROM sessions
             WHERE started_at >= ?
               AND activity_type IS NOT NULL{pclause}
          GROUP BY activity_type
          ORDER BY sessions DESC
            """,
            [since] + pparams,
        ).fetchall()
    breakdown = []
    for r in rows:
        sess = int(r["sessions"] or 0)
        shots = int(r["one_shots"] or 0)
        breakdown.append({
            "type": r["activity_type"],
            "sessions": sess,
            "cost_usd": float(r["cost"] or 0),
            "one_shot_rate": shots / sess if sess else 0,
        })
    top = breakdown[0]["type"] if breakdown else None
    return {"range": range, "days": days, "breakdown": breakdown, "top_type": top}


# --------------------------------------------------- yield analysis --
@router.get("/yield")
def yield_analysis(range: str = Query("30d"), provider: str = Query("all")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    pclause, pparams = _provider_clause(provider, alias="sessions")
    with readonly_cursor() as cur:
        rows = cur.execute(
            f"""
            SELECT yielded, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost
              FROM sessions
             WHERE started_at >= ?
               AND outcome IN ('ok', 'errored'){pclause}
          GROUP BY yielded
            """,
            [since] + pparams,
        ).fetchall()
        abandoned_rows = cur.execute(
            f"""
            SELECT id, cwd, started_at, cost_usd
              FROM sessions
             WHERE started_at >= ?
               AND outcome IN ('ok', 'errored')
               AND yielded = 0{pclause}
          ORDER BY cost_usd DESC
             LIMIT 20
            """,
            [since] + pparams,
        ).fetchall()

    buckets = {None: (0, 0.0), 0: (0, 0.0), 1: (0, 0.0), 2: (0, 0.0), -1: (0, 0.0)}
    for r in rows:
        key = r["yielded"]
        buckets[key] = (int(r["n"]), float(r["cost"]))

    unknown_n, unknown_cost   = buckets[None]
    abandoned_n, abandoned_cost = buckets[0]
    productive_n, productive_cost = buckets[1]
    reverted_n, reverted_cost = buckets[2]
    non_git_n, non_git_cost   = buckets[-1]

    analyzed = productive_n + abandoned_n + reverted_n
    yield_rate = productive_n / analyzed if analyzed else 0

    return {
        "range": range,
        "days": days,
        "productive":  {"sessions": productive_n, "cost_usd": productive_cost},
        "abandoned":   {"sessions": abandoned_n,  "cost_usd": abandoned_cost},
        "reverted":    {"sessions": reverted_n,   "cost_usd": reverted_cost},
        "non_git":     {"sessions": non_git_n,    "cost_usd": non_git_cost},
        "unknown":     {"sessions": unknown_n,    "cost_usd": unknown_cost},
        "yield_rate":  yield_rate,
        "top_abandoned": [
            {"id": r["id"], "cwd": r["cwd"], "started_at": r["started_at"], "cost_usd": float(r["cost_usd"] or 0)}
            for r in abandoned_rows
        ],
    }


# -------------------------------------------------- optimize / waste --
@router.get("/optimize")
def optimize() -> dict[str, Any]:
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT pattern, severity, detail, tokens_saved, fix_label, fix_text, detected_at
              FROM waste_findings
             ORDER BY
               CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
               tokens_saved DESC
            """
        ).fetchall()
    findings = [
        {
            "pattern":     r["pattern"],
            "severity":    r["severity"],
            "detail":      r["detail"],
            "tokens_saved": int(r["tokens_saved"] or 0),
            "fix_label":   r["fix_label"],
            "fix_text":    r["fix_text"],
            "detected_at": r["detected_at"],
        }
        for r in rows
    ]
    total_tokens = sum(f["tokens_saved"] for f in findings)
    penalty = sum({"high": 15, "medium": 7, "low": 3}.get(f["severity"], 0) for f in findings)
    health_score = max(0, min(100, 100 - min(80, penalty)))
    if health_score >= 90:   grade = "A"
    elif health_score >= 75: grade = "B"
    elif health_score >= 55: grade = "C"
    elif health_score >= 30: grade = "D"
    else:                    grade = "F"
    return {
        "findings":      findings,
        "total_tokens_saved": total_tokens,
        "health_score":  health_score,
        "health_grade":  grade,
        "last_scanned":  findings[0]["detected_at"] if findings else None,
    }


# ----------------------------------------------- gauge hero row data --
@router.get("/gauge")
def gauge_data() -> dict[str, Any]:
    today = _utc_today_prefix()
    since_30d, _, _ = resolve_range("30d")
    live_cutoff = time.time() - LIVE_WINDOW_SECONDS
    with readonly_cursor() as cur:
        # Burn: today cost vs 30d daily avg
        today_cost = float(
            cur.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS c FROM messages WHERE substr(ts,1,10)=?",
                (today,),
            ).fetchone()["c"]
        )
        day_costs = cur.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM messages WHERE ts>=? GROUP BY substr(ts,1,10)",
            (since_30d,),
        ).fetchall()
        avg_30d = sum(float(r["c"]) for r in day_costs) / 30.0 if day_costs else 0.0
        burn_value = round(min(100.0, (today_cost / avg_30d) * 50.0), 1) if avg_30d > 0 else 50.0

        # Discipline: premium-model share of *spend* over 30d. Token share was
        # the old measure and it lied once Opus got cheap and Fable arrived
        # above it — cost is what the target actually cares about.
        model_rows = cur.execute(
            """SELECT model,
                      COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_create_tokens),0) AS t,
                      COALESCE(SUM(cost_usd),0) AS c
               FROM messages WHERE ts>=? AND role='assistant' GROUP BY model""",
            (since_30d,),
        ).fetchall()
        total_t = sum(int(r["t"] or 0) for r in model_rows) or 1
        total_c = sum(float(r["c"] or 0) for r in model_rows)
        premium_rows = [r for r in model_rows if _classify_model(r["model"]) in PREMIUM_FAMILIES]
        premium_c = sum(float(r["c"] or 0) for r in premium_rows)
        premium_t = sum(int(r["t"] or 0) for r in premium_rows)
        premium_pct = premium_c / total_c if total_c else 0.0
        by_family_cost: dict[str, float] = defaultdict(float)
        for r in model_rows:
            by_family_cost[_classify_model(r["model"])] += float(r["c"] or 0)

        # Efficiency: cache hit rate 30d
        cr = cur.execute(
            """SELECT COALESCE(SUM(input_tokens),0) AS inp,
                      COALESCE(SUM(cache_read_tokens),0) AS cr,
                      COALESCE(SUM(cache_create_tokens),0) AS cc
               FROM messages WHERE ts>=?""",
            (since_30d,),
        ).fetchone()
        billable = int(cr["inp"] or 0) + int(cr["cr"] or 0) + int(cr["cc"] or 0)
        hit_rate = int(cr["cr"] or 0) / billable if billable else 0.0

        # Hygiene: from waste_findings
        findings = cur.execute("SELECT severity FROM waste_findings").fetchall()
        penalty = sum({"high": 15, "medium": 7, "low": 3}.get(r["severity"], 0) for r in findings)
        hygiene_score = max(0, min(100, 100 - min(80, penalty)))
        if hygiene_score >= 90:    grade = "A"
        elif hygiene_score >= 75:  grade = "B"
        elif hygiene_score >= 55:  grade = "C"
        elif hygiene_score >= 30:  grade = "D"
        else:                      grade = "F"

        # Live: active sessions
        live_n = cur.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE last_modified_ts>=?", (live_cutoff,)
        ).fetchone()["n"]

    return {
        "burn":        {"value": burn_value,          "today_usd": round(today_cost, 3), "avg_usd": round(avg_30d, 3)},
        "discipline":  {
            "value": round(premium_pct * 100, 1),
            "premium_cost_pct": round(premium_pct, 4),
            "premium_token_pct": round(premium_t / total_t, 4),
            "premium_cost_usd": round(premium_c, 3),
            "target_max": PREMIUM_COST_SHARE_TARGET,
            "families": list(PREMIUM_FAMILIES),
            "by_family_cost": {k: round(v, 3) for k, v in sorted(by_family_cost.items())},
        },
        "efficiency":  {"value": round(hit_rate*100, 1), "hit_rate": round(hit_rate, 4)},
        "hygiene":     {"value": hygiene_score,        "grade": grade, "score": hygiene_score},
        "live":        {"count": int(live_n)},
    }


# ------------------------------------------ 10. context heatmap (live) --
@router.get("/context-heatmap")
def context_heatmap() -> dict[str, Any]:
    cutoff = time.time() - LIVE_WINDOW_SECONDS
    with readonly_cursor() as cur:
        sessions = cur.execute(
            """
            SELECT id, model, cwd, last_modified_ts, last_user_message
              FROM sessions
             WHERE last_modified_ts >= ?
          ORDER BY last_modified_ts DESC
            """,
            (cutoff,),
        ).fetchall()
        out = []
        for s in sessions:
            # ORDER BY + LIMIT 1 on a bare SUM() is a no-op — SUM with no
            # GROUP BY always returns exactly one row, so this used to total
            # every assistant message in the whole session instead of reading
            # the last one. A long session could read many multiples of its
            # real context size (one live session measured 14.3M vs a 1M
            # window, i.e. 1435%). Row-select the last message, then read its
            # own token columns instead of aggregating.
            usage = cur.execute(
                """
                SELECT input_tokens AS i, output_tokens AS o,
                       cache_read_tokens AS cr, cache_create_tokens AS cc
                  FROM messages
                 WHERE session_id = ?
                   AND role = 'assistant'
              ORDER BY ts DESC
                 LIMIT 1
                """,
                (s["id"],),
            ).fetchone()
            # Current context size = the last assistant message's own
            # input + cache_read + cache_create — that message's API call
            # carried the full replayed context, so its token counts ARE the
            # context size at that point in the conversation.
            # A session can be live with only a user turn so far — no
            # assistant message yet to read a token count from.
            current = (
                int(usage["i"] or 0) + int(usage["cr"] or 0) + int(usage["cc"] or 0)
                if usage else 0
            )
            window = _context_window_for(s["model"])
            pct = (current / window) if window else 0
            status = "green"
            if pct >= 0.40:
                status = "red"
            elif pct >= 0.30:
                status = "amber"
            out.append(
                {
                    "session_id": s["id"],
                    "model": s["model"],
                    "cwd": s["cwd"],
                    "last_user_message": s["last_user_message"],
                    "current_tokens": current,
                    "window_size": window,
                    "pct": pct,
                    "status": status,
                }
            )
    return {"sessions": out, "compact_threshold": 0.40}


# -------------------------------------------------------- doctor check --
@router.get("/doctor")
def doctor() -> dict[str, Any]:
    import shutil, os
    from ..config import CLAUDE_PROJECTS_DIR

    with readonly_cursor() as cur:
        last_otel = cur.execute(
            "SELECT received_at FROM otel_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total_sessions = cur.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        disk = shutil.disk_usage(os.path.dirname(CLAUDE_PROJECTS_DIR))
    checks = [
        {
            "name": "python",
            "ok": True,
            "detail": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
        },
        {
            "name": "projects_dir",
            "ok": CLAUDE_PROJECTS_DIR.exists(),
            "detail": str(CLAUDE_PROJECTS_DIR),
        },
        {
            "name": "sessions_ingested",
            "ok": total_sessions > 0,
            "detail": f"{total_sessions} sessions in DB",
        },
        {
            "name": "otel_recent",
            "ok": True,  # opt-in feature, never a hard-fail
            "detail": (
                f"last OTEL at {last_otel['received_at']}" if last_otel
                else "OTEL not wired (optional; run ./scripts/setup-otel.sh to enable)"
            ),
        },
        {
            "name": "disk",
            "ok": disk.free > 100 * 1024 * 1024,
            "detail": f"{disk.free // (1024*1024)}MiB free",
        },
    ]
    return {"checks": checks, "all_green": all(c["ok"] for c in checks)}


# =========================================================================
# v2 PANELS
# =========================================================================


def _utc_today_prefix() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()  # 'YYYY-MM-DD'


def _tag_for_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    for prefix, tag in PROJECT_TAG_RULES:
        if cwd.startswith(prefix):
            return tag
    return None


# ------------------------------------------------------- 11. heatmap (84d) --
@router.get("/heatmap")
def activity_heatmap() -> dict[str, Any]:
    cutoff = (
        dt.datetime.now(dt.UTC) - dt.timedelta(weeks=HEATMAP_WEEKS)
    ).isoformat().replace("+00:00", "Z")
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT substr(ts, 1, 10) AS date,
                   COUNT(*) AS count
              FROM messages
             WHERE ts >= ?
          GROUP BY date
          ORDER BY date ASC
            """,
            (cutoff,),
        ).fetchall()
    days = [{"date": r["date"], "count": int(r["count"])} for r in rows]
    return {"weeks": HEATMAP_WEEKS, "days": days}


def _mc_avg_ctx_pct(cur, cutoff_ts: float) -> float:
    """Avg context% across live sessions. Shared by status_cards and mission_clock."""
    live_sessions = cur.execute(
        "SELECT id, model FROM sessions WHERE last_modified_ts >= ?",
        (cutoff_ts,),
    ).fetchall()
    pcts: list[float] = []
    for s in live_sessions:
        usage = cur.execute(
            """
            SELECT input_tokens AS i,
                   cache_read_tokens AS cr,
                   cache_create_tokens AS cc
              FROM messages
             WHERE session_id = ?
               AND role = 'assistant'
          ORDER BY ts DESC
             LIMIT 1
            """,
            (s["id"],),
        ).fetchone()
        if not usage:
            continue
        current = int(usage["i"] or 0) + int(usage["cr"] or 0) + int(usage["cc"] or 0)
        window = _context_window_for(s["model"])
        if window:
            pcts.append(min(1.0, current / window))
    return sum(pcts) / len(pcts) if pcts else 0.0


# ---------------------------------------------------- 12. status cards ---
@router.get("/status-cards")
def status_cards() -> dict[str, Any]:
    cutoff = time.time() - LIVE_WINDOW_SECONDS
    today_prefix = _utc_today_prefix()
    with readonly_cursor() as cur:
        live_row = cur.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE last_modified_ts >= ?",
            (cutoff,),
        ).fetchone()
        tokens_row = cur.execute(
            """
            SELECT COALESCE(SUM(
                input_tokens + output_tokens + cache_read_tokens + cache_create_tokens
            ), 0) AS t,
            COALESCE(SUM(cost_usd), 0) AS c
              FROM messages
             WHERE substr(ts, 1, 10) = ?
            """,
            (today_prefix,),
        ).fetchone()
        avg_ctx_pct = _mc_avg_ctx_pct(cur, cutoff)
        # Top project today (by session count in live window)
        top_row = cur.execute(
            """
            SELECT cwd, COUNT(*) AS n
              FROM sessions
             WHERE last_modified_ts >= ?
               AND cwd IS NOT NULL
          GROUP BY cwd
          ORDER BY n DESC
             LIMIT 1
            """,
            (cutoff,),
        ).fetchone()
    return {
        "live_sessions": int(live_row["n"] or 0),
        "tokens_today": int(tokens_row["t"] or 0),
        "cost_today_usd": float(tokens_row["c"] or 0),
        "avg_ctx_pct": avg_ctx_pct,
        "top_project": top_row["cwd"] if top_row else None,
    }


# ---------------------------------------------------- 13. two-strike ---
@router.get("/two-strike")
def two_strike(range: str = Query("30d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT tool_name,
                   substr(error_preview, 1, 80) AS error_preview_prefix,
                   COUNT(*) AS hits,
                   MAX(started_at) AS last_seen
              FROM tool_calls
             WHERE started_at >= ?
               AND is_error = 1
               AND error_preview IS NOT NULL
          GROUP BY tool_name, error_preview_prefix
            HAVING COUNT(*) >= 2
          ORDER BY hits DESC
             LIMIT 20
            """,
            (since,),
        ).fetchall()
    fingerprints = [
        {
            "tool_name": r["tool_name"],
            "error_preview_prefix": r["error_preview_prefix"],
            "hits": int(r["hits"]),
            "last_seen": r["last_seen"],
        }
        for r in rows
    ]
    return {"range": range, "days": days, "fingerprints": fingerprints}


# ---------------------------------------------------- 14. revenue lens ---
@router.get("/revenue-lens")
def revenue_lens(range: str = Query("7d")) -> dict[str, Any]:
    since, _, days = resolve_range(range)
    with readonly_cursor() as cur:
        rows = cur.execute(
            """
            SELECT s.cwd AS cwd,
                   COUNT(DISTINCT s.id) AS sessions,
                   COALESCE(SUM(
                     m.input_tokens + m.output_tokens +
                     m.cache_read_tokens + m.cache_create_tokens
                   ), 0) AS tokens
              FROM sessions s
              LEFT JOIN messages m ON m.session_id = s.id
             WHERE s.started_at >= ?
          GROUP BY s.cwd
            """,
            (since,),
        ).fetchall()
    tag_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"sessions": 0, "tokens": 0}
    )
    untagged = {"sessions": 0, "tokens": 0}
    for r in rows:
        tag = _tag_for_cwd(r["cwd"])
        sessions = int(r["sessions"] or 0)
        tokens = int(r["tokens"] or 0)
        if tag is None:
            untagged["sessions"] += sessions
            untagged["tokens"] += tokens
        else:
            tag_totals[tag]["sessions"] += sessions
            tag_totals[tag]["tokens"] += tokens
    tags = [
        {"tag": tag, "sessions": data["sessions"], "tokens": data["tokens"]}
        for tag, data in tag_totals.items()
    ]
    tags.sort(key=lambda t: t["tokens"], reverse=True)
    return {"range": range, "days": days, "tags": tags, "untagged": untagged}


# ---------------------------------------------------- 15. vault ribbon ---
@router.get("/vault-ribbon")
def vault_ribbon() -> dict[str, Any]:
    # rglob order is filesystem-defined (not mtime-sorted); to guarantee the
    # real top-10 by mtime we must stat every candidate before sorting. Cap
    # exists so a runaway vault can't block the request.
    import heapq

    if not OBSIDIAN_DIR.exists():
        return {"files": [], "scanned": 0, "capped": False, "vault_exists": False}
    heap: list[tuple[float, str]] = []  # min-heap of (mtime, name), size 10
    scanned = 0
    capped = False
    try:
        for path in OBSIDIAN_DIR.rglob("*.md"):
            scanned += 1
            if scanned > OBSIDIAN_MAX_SCAN_FILES:
                capped = True
                break
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if len(heap) < 10:
                heapq.heappush(heap, (mtime, path.name))
            elif mtime > heap[0][0]:
                heapq.heapreplace(heap, (mtime, path.name))
    except OSError:
        return {"files": [], "scanned": scanned, "capped": capped, "vault_exists": True}
    top = sorted(heap, key=lambda e: e[0], reverse=True)
    files = [
        {
            "name": name,
            "mtime_iso": dt.datetime.fromtimestamp(mtime, dt.UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }
        for (mtime, name) in top
    ]
    return {
        "files": files,
        "scanned": scanned if not capped else OBSIDIAN_MAX_SCAN_FILES,
        "capped": capped,
        "vault_exists": True,
    }


# ---------------------------------------------------- 16. suggestion box ---
_EMPTY_SUGGESTIONS_RESP: dict[str, Any] = {
    "schema_version": 1,
    "generated_at": None,
    "generator_model": None,
    "notes": None,
    "recommendations": [],
    "empty": True,
}


@router.get("/suggestions")
def suggestions() -> dict[str, Any]:
    try:
        data = json.loads(SUGGESTIONS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _EMPTY_SUGGESTIONS_RESP
    recs = data.get("recommendations", [])
    return {
        "schema_version": data.get("schema_version", 1),
        "generated_at": data.get("generated_at"),
        "generator_model": data.get("generator_model"),
        "notes": data.get("notes"),
        "recommendations": recs,
        "empty": len(recs) == 0,
    }


# =========================================================================
# v3 MISSION CLOCK (pass_2_a) — kicker 12 slot, replaces StatusCards in grid
# =========================================================================
# OUTBOUND NETWORK FORBIDDEN — local reads only. No urllib / httpx / requests /
# socket.connect in this section. Rate limits come from user env vars.
# =========================================================================


def _mc_caps() -> dict[str, Any]:
    """Read token caps from env at call time so caps can be tuned without service restart.
    caps_configured=False if ANY Opus/Sonnet cap is zero/unset.
    """
    opus_weekly   = int(os.environ.get("OPUS_WEEKLY_CAP_TOKENS",   "0"))
    opus_5h       = int(os.environ.get("OPUS_5H_CAP_TOKENS",       "0"))
    sonnet_weekly = int(os.environ.get("SONNET_WEEKLY_CAP_TOKENS", "0"))
    sonnet_5h     = int(os.environ.get("SONNET_5H_CAP_TOKENS",     "0"))
    configured = bool(opus_weekly and opus_5h and sonnet_weekly and sonnet_5h)
    return {
        "opus_weekly":   opus_weekly,
        "opus_5h":       opus_5h,
        "sonnet_weekly": sonnet_weekly,
        "sonnet_5h":     sonnet_5h,
        "configured":    configured,
    }


def _mc_rules(
    weekly_pct: float,
    five_hour_pct: float,
    opus_share_24h: float,
    burn_rate_ratio: float,
    avg_ctx_pct: float,
) -> dict[str, Any]:
    """7-tier rules engine. First match wins. Pure function, no I/O."""
    if weekly_pct >= 0.90 and opus_share_24h >= 0.40:
        return {"tier": 1, "color": "red",   "directive": "DROP OPUS",       "slash_cmd": "/model sonnet"}
    if five_hour_pct >= 0.90:
        return {"tier": 2, "color": "red",   "directive": "PAUSE OR SWITCH", "slash_cmd": "/model haiku"}
    if weekly_pct >= 0.80:
        return {"tier": 3, "color": "amber", "directive": "THROTTLE OPUS",   "slash_cmd": "/model sonnet"}
    if five_hour_pct >= 0.70:
        return {"tier": 4, "color": "amber", "directive": "EASE UP",         "slash_cmd": None}
    if burn_rate_ratio >= 3.0:
        return {"tier": 5, "color": "amber", "directive": "SPIKE DETECTED",  "slash_cmd": None}
    if avg_ctx_pct >= 0.40:
        return {"tier": 6, "color": "amber", "directive": "RUN /compact",    "slash_cmd": "/compact"}
    return     {"tier": 7, "color": "green", "directive": "OK",              "slash_cmd": None}


def _mc_compute(cur, caps: dict[str, Any], now_ts: float) -> dict[str, Any]:
    # TODO pass_2_b: promote burn rate to 7d median 15m windows for noise resistance.
    cutoff_5h   = now_ts - 5 * 3600
    cutoff_7d   = now_ts - 7 * 86400
    cutoff_1h   = now_ts - 1 * 3600
    cutoff_24h  = now_ts - 24 * 3600
    live_cutoff = now_ts - LIVE_WINDOW_SECONDS

    def _iso(ts: float) -> str:
        return dt.datetime.fromtimestamp(ts, dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _tokens(since_ts: float, family: str) -> int:
        row = cur.execute(
            """
            SELECT COALESCE(SUM(
                input_tokens + output_tokens + cache_read_tokens + cache_create_tokens
            ), 0) AS t
              FROM messages
             WHERE ts >= ?
               AND role = 'assistant'
               AND model LIKE ?
            """,
            (_iso(since_ts), f"claude-{family}%"),
        ).fetchone()
        return int(row["t"] or 0)

    opus_weekly_used   = _tokens(cutoff_7d, "opus")
    opus_5h_used       = _tokens(cutoff_5h, "opus")
    sonnet_weekly_used = _tokens(cutoff_7d, "sonnet")
    sonnet_5h_used     = _tokens(cutoff_5h, "sonnet")

    weekly_pct = 0.0
    five_hour_pct = 0.0
    if caps["configured"]:
        weekly_pct = max(
            opus_weekly_used   / caps["opus_weekly"],
            sonnet_weekly_used / caps["sonnet_weekly"],
        )
        five_hour_pct = max(
            opus_5h_used   / caps["opus_5h"],
            sonnet_5h_used / caps["sonnet_5h"],
        )

    opus_24h   = _tokens(cutoff_24h, "opus")
    sonnet_24h = _tokens(cutoff_24h, "sonnet")
    haiku_24h  = _tokens(cutoff_24h, "haiku")
    total_24h  = opus_24h + sonnet_24h + haiku_24h
    opus_share_24h = (opus_24h / total_24h) if total_24h else 0.0

    tokens_1h_row = cur.execute(
        """
        SELECT COALESCE(SUM(
            input_tokens + output_tokens + cache_read_tokens + cache_create_tokens
        ), 0) AS t
          FROM messages
         WHERE ts >= ?
           AND role = 'assistant'
        """,
        (_iso(cutoff_1h),),
    ).fetchone()
    tokens_1h = int(tokens_1h_row["t"] or 0)
    avg_per_hour_24h = total_24h / 24.0
    burn_rate_ratio = (tokens_1h / avg_per_hour_24h) if avg_per_hour_24h else 0.0

    avg_ctx_pct = _mc_avg_ctx_pct(cur, live_cutoff)

    eta_seconds: float | None = None
    if caps["configured"] and tokens_1h > 0:
        remaining_weekly = min(
            caps["opus_weekly"]   - opus_weekly_used,
            caps["sonnet_weekly"] - sonnet_weekly_used,
        )
        remaining_5h = min(
            caps["opus_5h"]   - opus_5h_used,
            caps["sonnet_5h"] - sonnet_5h_used,
        )
        remaining = max(0, min(remaining_weekly, remaining_5h))
        tokens_per_sec = tokens_1h / 3600.0
        if tokens_per_sec > 0:
            eta_seconds = remaining / tokens_per_sec

    action = _mc_rules(
        weekly_pct=weekly_pct,
        five_hour_pct=five_hour_pct,
        opus_share_24h=opus_share_24h,
        burn_rate_ratio=burn_rate_ratio,
        avg_ctx_pct=avg_ctx_pct,
    )

    return {
        "weekly_pct": round(weekly_pct, 4),
        "five_hour_pct": round(five_hour_pct, 4),
        "burn_rate_ratio": round(burn_rate_ratio, 3),
        "avg_ctx_pct": round(avg_ctx_pct, 4),
        "opus_share_24h": round(opus_share_24h, 4),
        "eta_seconds": round(eta_seconds) if eta_seconds is not None else None,
        "caps_configured": caps["configured"],
        "action": action,
    }


@router.get("/mission-clock")
def mission_clock() -> dict[str, Any]:
    caps = _mc_caps()
    now_ts = time.time()
    with readonly_cursor() as cur:
        return _mc_compute(cur, caps, now_ts)


# ----------------------------------------- plan limits (Anthropic-side) --
# Everything else in this dashboard is derived from local JSONL. Plan
# utilisation is not in those files — it only exists server-side — so this is
# the one endpoint that leaves the machine. The token is the Claude Code OAuth
# token already on disk; it is read per-request and never returned to the
# frontend.
ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CREDENTIALS_FILE = HOME / ".claude" / ".credentials.json"
_USAGE_TTL = 60.0  # seconds; matches how coarsely the numbers actually move
_usage_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _oauth_token() -> str | None:
    try:
        creds = json.loads(_CREDENTIALS_FILE.read_text())
    except (OSError, ValueError):
        return None
    oauth = creds.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt")
    # expiresAt is epoch millis. A stale token would just 401, but checking is
    # cheaper than a round trip and gives the panel a precise reason.
    if expires_at and float(expires_at) / 1000.0 < time.time():
        return None
    return token


@router.get("/plan-usage")
def plan_usage() -> dict[str, Any]:
    import urllib.error
    import urllib.request

    now = time.time()
    if _usage_cache["payload"] is not None and now - _usage_cache["ts"] < _USAGE_TTL:
        return _usage_cache["payload"]

    token = _oauth_token()
    if not token:
        return {
            "available": False,
            "reason": "No valid Claude Code OAuth token in ~/.claude/.credentials.json",
            "limits": [],
        }

    req = urllib.request.Request(
        ANTHROPIC_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-dashboard/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"available": False, "reason": f"Anthropic returned {exc.code}", "limits": []}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "limits": []}

    # `limits[]` is the current shape; the flat five_hour/seven_day keys are the
    # older one. Prefer limits[], synthesise it from the flat keys if absent.
    limits = raw.get("limits") or []
    if not limits:
        for kind, key in (("session", "five_hour"), ("weekly_all", "seven_day")):
            block = raw.get(key)
            if isinstance(block, dict) and block.get("utilization") is not None:
                limits.append(
                    {
                        "kind": kind,
                        "group": "session" if kind == "session" else "weekly",
                        "percent": block["utilization"],
                        "severity": "normal",
                        "resets_at": block.get("resets_at"),
                        "scope": None,
                    }
                )

    extra = raw.get("extra_usage") or {}
    payload = {
        "available": True,
        "limits": [
            {
                "kind": l.get("kind"),
                "group": l.get("group"),
                "percent": l.get("percent"),
                "severity": l.get("severity", "normal"),
                "resets_at": l.get("resets_at"),
                "scope_label": ((l.get("scope") or {}).get("model") or {}).get("display_name"),
            }
            for l in limits
        ],
        "extra_usage": {
            "enabled": bool(extra.get("is_enabled")),
            "utilization": extra.get("utilization"),
            "used_credits": extra.get("used_credits"),
            "monthly_limit": extra.get("monthly_limit"),
        },
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    _usage_cache.update(ts=now, payload=payload)
    return payload


# =========================================================================
# v4 SYSTEM HEALTH (live diagnostics)
# =========================================================================


@router.get("/system-health")
def system_health() -> dict[str, Any]:
    """Return live system metrics (CPU, RAM, disk) with color status."""
    with readonly_cursor() as cur:
        # Get the latest metrics
        row = cur.execute(
            """
            SELECT cpu_percent, memory_percent, memory_available_mb,
                   disk_percent, load_avg_1min, process_count, timestamp
              FROM system_metrics
             ORDER BY timestamp DESC
             LIMIT 1
            """
        ).fetchone()

    if not row:
        return {
            "available": False,
            "message": "System metrics not yet available",
        }

    cpu_pct = row["cpu_percent"] or 0
    mem_pct = row["memory_percent"] or 0
    disk_pct = row["disk_percent"] or 0

    # Color logic: green <70%, amber 70-85%, red >85%
    def get_status(pct: float) -> str:
        if pct >= 85:
            return "red"
        elif pct >= 70:
            return "amber"
        return "green"

    return {
        "available": True,
        "timestamp": row["timestamp"],
        "cpu": {
            "percent": round(cpu_pct, 1),
            "status": get_status(cpu_pct),
        },
        "memory": {
            "percent": round(mem_pct, 1),
            "available_mb": row["memory_available_mb"],
            "status": get_status(mem_pct),
        },
        "disk": {
            "percent": round(disk_pct, 1),
            "status": get_status(disk_pct),
        },
        "load": round(row["load_avg_1min"] or 0, 2),
        "process_count": row["process_count"],
    }


@router.get("/hook-status")
def hook_status() -> dict[str, Any]:
    """Return current hook enable/disable status."""
    with readonly_cursor() as cur:
        # Get latest status for each hook
        rows = cur.execute(
            """
            SELECT DISTINCT hook_name, status, last_ran_at
              FROM hook_executions
             WHERE timestamp = (SELECT MAX(timestamp) FROM hook_executions)
             ORDER BY hook_name
            """
        ).fetchall()

    hooks = [
        {
            "name": r["hook_name"],
            "status": r["status"],  # enabled | disabled
            "last_ran_at": r["last_ran_at"],
        }
        for r in rows
    ]
    return {"hooks": hooks}


@router.get("/process-monitor")
def process_monitor(limit: int = Query(5)) -> dict[str, Any]:
    """Return top N processes by RAM usage."""
    from ..ingest.system_metrics import get_top_processes

    try:
        processes = get_top_processes(limit)
        return {"available": True, "processes": processes}
    except Exception as e:
        return {"available": False, "message": str(e), "processes": []}


# =========================================================================
# INTELLIGENCE — live advisory panel, no Haiku calls, pure SQL on load
# =========================================================================

def _cost(inp: int, out: int, cr: int, cw: int, model: str) -> float:
    return get_cost_usd(model, inp, out, cr, cw)


@router.get("/intelligence")
def intelligence() -> dict[str, Any]:
    """Live advisory panel — 6 SQL queries, no external calls, fresh on every load."""
    recommendations: list[dict[str, Any]] = []

    with readonly_cursor() as cur:

        # 1 — Session error rate: today vs baseline
        cur.execute("""
            SELECT
                ROUND(100.0 * SUM(CASE WHEN DATE(started_at) = DATE('now','localtime') AND outcome='errored' THEN 1 ELSE 0 END)
                    / MAX(1, SUM(CASE WHEN DATE(started_at) = DATE('now','localtime') THEN 1 ELSE 0 END)), 1) today_pct,
                ROUND(100.0 * SUM(CASE WHEN outcome='errored' THEN 1 ELSE 0 END) / MAX(1,COUNT(*)), 1) baseline_pct,
                SUM(CASE WHEN DATE(started_at) = DATE('now','localtime') AND outcome='errored' THEN 1 ELSE 0 END) today_errors,
                SUM(CASE WHEN DATE(started_at) = DATE('now','localtime') THEN 1 ELSE 0 END) today_total
            FROM sessions
        """)
        row = cur.fetchone()
        if row and row["today_total"] >= 3 and row["today_pct"] is not None:
            today_pct = row["today_pct"]
            baseline = row["baseline_pct"]
            if today_pct > baseline * 1.5:
                recommendations.append({
                    "id": "error-rate-elevated",
                    "title": "Error rate elevated today",
                    "body": f"{row['today_errors']} of {row['today_total']} sessions errored today ({today_pct}% vs {baseline}% baseline). Something is failing more than usual.",
                    "action": "Check the Session Outcomes panel for details",
                    "severity": "high",
                })

        # 2 — Flaky tools (error rate > 15%, more than 50 uses)
        cur.execute("""
            SELECT tool_name, COUNT(*) uses,
                   ROUND(100.0 * SUM(is_error) / MAX(1,COUNT(*)), 1) error_pct,
                   ROUND(AVG(CASE WHEN duration_ms > 0 THEN duration_ms END) / 1000.0, 1) avg_sec
            FROM tool_calls
            GROUP BY tool_name
            HAVING uses > 50 AND error_pct > 15
            ORDER BY error_pct DESC
            LIMIT 3
        """)
        flaky = cur.fetchall()
        for f in flaky:
            recommendations.append({
                "id": f"flaky-tool-{f['tool_name']}",
                "title": f"{f['tool_name']} fails {f['error_pct']}% of the time",
                "body": f"{f['uses']:,} calls, {f['error_pct']}% error rate, {f['avg_sec']}s average. Most failures are silent retries or abandoned attempts.",
                "action": "WebFetch → switch to WebSearch or Firecrawl for better reliability" if f["tool_name"] == "WebFetch" else f"Review {f['tool_name']} error patterns in Tool Latency panel",
                "severity": "medium",
            })

        # 3 — Premium (Fable + Opus) spend vs target (7-day window). Message
        # count was the old measure: it treated one Fable reply and one Haiku
        # reply as equal weight, and did not know Fable existed at all.
        cur.execute(
            """
            SELECT ROUND(100.0 * COALESCE(SUM(CASE WHEN model LIKE '%opus%' OR model LIKE '%fable%'
                                               THEN cost_usd END), 0)
                / NULLIF(SUM(cost_usd), 0), 1) AS premium_pct
              FROM messages
             WHERE role='assistant' AND ts >= datetime('now', '-7 days')
            """
        )
        row = cur.fetchone()
        target_pct = PREMIUM_COST_SHARE_TARGET * 100
        if row and row["premium_pct"] is not None:
            premium_pct = row["premium_pct"]
            multiplier = round(premium_pct / target_pct, 1)
            if premium_pct > target_pct * 1.25:
                sev = "high" if premium_pct > target_pct * 2 else "medium"
                recommendations.append({
                    "id": "premium-over-target",
                    "title": f"Premium models at {premium_pct}% of spend — target is {target_pct:.0f}%",
                    "body": f"{multiplier}× over target this week. Sonnet handles most implementation work at a fraction of the cost. Switch for feature work; keep Fable and Opus for architecture.",
                    "action": "Use Sonnet for implementation sessions — /model sonnet",
                    "severity": sev,
                })
            elif premium_pct <= target_pct:
                recommendations.append({
                    "id": "premium-on-target",
                    "title": f"Premium models at {premium_pct}% of spend — on target",
                    "body": f"Model mix is healthy this week. Fable and Opus together are within the {target_pct:.0f}% target.",
                    "action": None,
                    "severity": "ok",
                })

        # 4 — Context rot: /clear usage this week
        cur.execute("""
            SELECT COUNT(*) clears
            FROM skill_invocations
            WHERE skill_name = 'clear' AND ts >= datetime('now', '-7 days')
        """)
        row = cur.fetchone()
        if row and row["clears"] >= 5:
            recommendations.append({
                "id": "context-rot",
                "title": f"You cleared context {row['clears']} times this week",
                "body": "/clear is a signal you're hitting context rot. Compacting at 40% preserves more state than clearing.",
                "action": "Use /compact with a preserve list before context fills — don't wait until forced",
                "severity": "low",
            })

        # 5 — Agent timeouts (calls > 60s)
        cur.execute("""
            SELECT COUNT(*) timeouts, ROUND(AVG(duration_ms)/1000.0,0) avg_sec
            FROM tool_calls
            WHERE tool_name = 'Agent' AND duration_ms > 60000
        """)
        row = cur.fetchone()
        if row and row["timeouts"] and row["timeouts"] >= 3:
            recommendations.append({
                "id": "agent-timeouts",
                "title": f"Agent calls timing out ({row['timeouts']} over 60s)",
                "body": f"Average {row['avg_sec']}s for long-running Agent calls. Large agent tasks frequently hit the 600s limit.",
                "action": "Break large Agent tasks into smaller subtasks with focused prompts",
                "severity": "medium",
            })

        # 6 — Cache efficiency
        cur.execute("""
            SELECT
                ROUND(100.0 * SUM(cache_read_tokens)
                    / MAX(1, SUM(input_tokens + cache_read_tokens)), 1) hit_rate
            FROM messages
            WHERE ts >= datetime('now', '-7 days') AND role = 'assistant'
        """)
        row = cur.fetchone()
        if row and row["hit_rate"] is not None:
            hit_rate = row["hit_rate"]
            if hit_rate < 60:
                recommendations.append({
                    "id": "cache-low",
                    "title": f"Cache hit rate low ({hit_rate}% — target ≥70%)",
                    "body": "Low cache efficiency means you're paying full input prices more often. Usually caused by long sessions without compaction or frequent context clears.",
                    "action": "Compact earlier and keep system prompts stable across sessions",
                    "severity": "medium",
                })

    # Sort: high → medium → low → ok
    order = {"high": 0, "medium": 1, "low": 2, "ok": 3}
    recommendations.sort(key=lambda r: order.get(r["severity"], 4))

    return {
        "recommendations": recommendations,
        "count": len(recommendations),
        "actionable": sum(1 for r in recommendations if r["severity"] != "ok"),
    }


# =========================================================================
# SESSION BROWSER — searchable full history
# =========================================================================

_SUMMARIES_DIR = HOME / ".claude" / "summaries"


@router.get("/sessions-browse")
def sessions_browse(
    q: str = Query(""),
    project: str = Query(""),
    limit: int = Query(100),
    offset: int = Query(0),
) -> dict[str, Any]:
    where_parts: list[str] = []
    params: list[Any] = []
    if q:
        where_parts.append("(last_user_message LIKE ? OR cwd LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if project:
        where_parts.append("cwd = ?")
        params.append(project)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with readonly_cursor() as cur:
        total = int(
            cur.execute(
                f"SELECT COUNT(*) AS n FROM sessions {where}", params
            ).fetchone()["n"]
        )
        rows = cur.execute(
            f"""
            SELECT id, started_at, cwd, last_user_message, outcome, cost_usd
              FROM sessions
            {where}
          ORDER BY started_at DESC
             LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
        project_rows = cur.execute(
            "SELECT DISTINCT cwd FROM sessions WHERE cwd IS NOT NULL ORDER BY cwd"
        ).fetchall()
    return {
        "total": total,
        "sessions": [dict(r) for r in rows],
        "projects": [r["cwd"] for r in project_rows],
        "limit": limit,
        "offset": offset,
    }


@router.get("/session-summary/{session_id}")
def session_summary(session_id: str) -> dict[str, Any]:
    if not _SUMMARIES_DIR.exists():
        return {"found": False, "content": None}
    prefix = session_id[:8]
    matches = list(_SUMMARIES_DIR.glob(f"*-{prefix}.md"))
    if not matches:
        return {"found": False, "content": None}
    content = matches[0].read_text(errors="replace")[:4000]
    return {"found": True, "content": content, "filename": matches[0].name}
