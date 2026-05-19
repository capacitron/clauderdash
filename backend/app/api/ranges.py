"""Shared range handling. `today` | `7d` | `30d` | `84d`."""
from __future__ import annotations

import datetime as dt

RANGES = {"today": 1, "7d": 7, "30d": 30, "84d": 84}


def resolve_range(range_key: str) -> tuple[str, str, int]:
    """Return (since_iso, until_iso, days) for a UTC range."""
    days = RANGES.get(range_key, 7)
    now = dt.datetime.now(dt.UTC)
    if range_key == "today":
        since = dt.datetime(now.year, now.month, now.day, tzinfo=dt.UTC)
    else:
        since = now - dt.timedelta(days=days)
    return since.isoformat(), now.isoformat(), days


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return float(sorted_values[f])
    return float(sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f))
