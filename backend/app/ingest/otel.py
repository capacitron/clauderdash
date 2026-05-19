"""OTLP/HTTP JSON receiver endpoints.

Claude Code posts to /v1/logs and /v1/metrics when telemetry is enabled with:
  CLAUDE_CODE_ENABLE_TELEMETRY=1
  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:7777
  OTEL_EXPORTER_OTLP_PROTOCOL=http/json

We store the firehose verbatim + a normalised attribute map so panels can query it.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, Request

from ..db import connect

_UTC = dt.timezone.utc


def _now_iso() -> str:
    return dt.datetime.now(_UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

log = logging.getLogger(__name__)
router = APIRouter()


def _flatten_attributes(attrs_list: list[dict]) -> dict[str, object]:
    """OTLP attribute encoding → flat dict."""
    out: dict[str, object] = {}
    for a in attrs_list or []:
        if not isinstance(a, dict):
            continue
        key = a.get("key")
        value = a.get("value") or {}
        if not key or not isinstance(value, dict):
            continue
        if "stringValue" in value:
            out[key] = value["stringValue"]
        elif "intValue" in value:
            try:
                out[key] = int(value["intValue"])
            except (TypeError, ValueError):
                out[key] = value["intValue"]
        elif "doubleValue" in value:
            out[key] = value["doubleValue"]
        elif "boolValue" in value:
            out[key] = bool(value["boolValue"])
        elif "arrayValue" in value:
            out[key] = value["arrayValue"]
        elif "kvlistValue" in value:
            out[key] = _flatten_attributes(value["kvlistValue"].get("values", []))
    return out


def _insert_otel(
    kind: str, event_name: str | None, session_id: str | None, ts: str, attrs: dict
) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO otel_events(kind, event_name, session_id, ts, attributes, received_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                event_name,
                session_id,
                ts,
                json.dumps(attrs, default=str),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


@router.post("/v1/logs")
async def receive_logs(request: Request):
    body = await request.json()
    count = 0
    for rl in body.get("resourceLogs", []) or []:
        resource_attrs = _flatten_attributes(rl.get("resource", {}).get("attributes", []))
        for sl in rl.get("scopeLogs", []) or []:
            for record in sl.get("logRecords", []) or []:
                attrs = _flatten_attributes(record.get("attributes", []))
                merged = {**resource_attrs, **attrs}
                nanos = record.get("timeUnixNano") or record.get("observedTimeUnixNano")
                ts = _nanos_to_iso(nanos)
                event_name = (
                    attrs.get("event.name")
                    or record.get("body", {}).get("stringValue")
                    or "log"
                )
                session_id = merged.get("session.id") or merged.get("sessionId")
                _insert_otel("log", str(event_name), str(session_id) if session_id else None,
                             ts, merged)
                count += 1
    return {"accepted": count}


@router.post("/v1/metrics")
async def receive_metrics(request: Request):
    body = await request.json()
    count = 0
    for rm in body.get("resourceMetrics", []) or []:
        resource_attrs = _flatten_attributes(rm.get("resource", {}).get("attributes", []))
        for sm in rm.get("scopeMetrics", []) or []:
            for metric in sm.get("metrics", []) or []:
                name = metric.get("name") or "metric"
                for dp in _iter_datapoints(metric):
                    attrs = _flatten_attributes(dp.get("attributes", []))
                    merged = {**resource_attrs, **attrs, "metric.value": _dp_value(dp)}
                    ts = _nanos_to_iso(dp.get("timeUnixNano"))
                    session_id = merged.get("session.id") or merged.get("sessionId")
                    _insert_otel("metric", name, str(session_id) if session_id else None,
                                 ts, merged)
                    count += 1
    return {"accepted": count}


@router.post("/v1/traces")
async def receive_traces(request: Request):
    # Not used by Claude Code today; accept to be a good OTLP citizen.
    await request.body()
    return {"accepted": 0}


def _iter_datapoints(metric: dict):
    for key in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
        body = metric.get(key)
        if isinstance(body, dict):
            for dp in body.get("dataPoints", []) or []:
                yield dp


def _dp_value(dp: dict):
    for key in ("asInt", "asDouble", "sum", "count"):
        if key in dp:
            try:
                return float(dp[key])
            except (TypeError, ValueError):
                return dp[key]
    return None


def _nanos_to_iso(nanos) -> str:
    try:
        n = int(nanos) if nanos is not None else 0
    except (TypeError, ValueError):
        return _now_iso()
    if n <= 0:
        return _now_iso()
    return (
        dt.datetime.fromtimestamp(n / 1_000_000_000, _UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
