"""Per-provider call accounting.

Free tiers are the binding constraint on how big a regularly-refreshed
watchlist can be, so every outbound provider call is counted before it is made
and the chain skips a provider that would exceed its daily budget. Counts live
in DuckDB so they survive restarts and the dashboard can read them directly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import db

# None = no meaningful documented limit. Numbers are calls per calendar day.
DAILY_LIMITS: dict[str, int | None] = {
    "tiingo": 500,        # free tier: 500 unique symbols/day
    "fmp": 250,           # free tier: 250 calls/day
    "fiscal_ai": 250,     # free tier: 250 requests/day
    "twelve_data": 800,   # free tier: 800 calls/day
    "alpaca": None,
    "fred": None,         # documented as unlimited in practice
    "sec_edgar": None,    # no key; fair-use rate limit, not a daily cap
    "cnbv": None,         # public regulator portal, no published cap
    "yfinance": None,     # unofficial, no published limit
}


# Some tiers throttle per hour, and that is the binding constraint during a
# bulk backfill long before the daily cap is reached. Tiingo answered 429 on
# 173 of 270 requests in one run while the daily counter still showed budget.
HOURLY_LIMITS: dict[str, int | None] = {
    "tiingo": 50,
    "fmp": None,
    "fiscal_ai": None,
    "twelve_data": 8,     # free tier: 8 requests/minute; treated as a floor here
    "alpaca": None,
    "fred": None,
    "sec_edgar": None,
    "cnbv": None,
    "yfinance": None,
}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def used_last_hour(provider: str) -> int:
    """Calls logged for this provider in the trailing hour."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    with db.connect() as con:
        row = con.execute(
            """SELECT COUNT(*) FROM fetch_log
               WHERE provider = ? AND run_at >= ? AND status IN ('ok', 'failed')""",
            [provider, since],
        ).fetchone()
    return int(row[0]) if row else 0


def hourly_remaining(provider: str) -> int | None:
    limit = HOURLY_LIMITS.get(provider)
    return None if limit is None else max(0, limit - used_last_hour(provider))


def used(provider: str, on: date | None = None) -> int:
    with db.connect() as con:
        row = con.execute(
            "SELECT calls FROM quota_usage WHERE provider = ? AND usage_date = ?",
            [provider, on or _today()],
        ).fetchone()
    return int(row[0]) if row else 0


def remaining(provider: str) -> int | None:
    limit = DAILY_LIMITS.get(provider)
    return None if limit is None else max(0, limit - used(provider))


def has_budget(provider: str, need: int = 1) -> bool:
    """True only if both the daily and the trailing-hour budgets allow it."""
    daily = remaining(provider)
    if daily is not None and daily < need:
        return False
    hourly = hourly_remaining(provider)
    return hourly is None or hourly >= need


def record(provider: str, calls: int = 1) -> None:
    """Count calls actually issued. Called by the sources themselves."""
    today = _today()
    with db.write_lock(), db.connect() as con:
        con.execute(
            """
            INSERT INTO quota_usage (provider, usage_date, calls)
            VALUES (?, ?, ?)
            ON CONFLICT (provider, usage_date)
            DO UPDATE SET calls = quota_usage.calls + EXCLUDED.calls
            """,
            [provider, today, calls],
        )


def reset_at() -> str:
    """UTC midnight, when every daily budget rolls over."""
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc).isoformat()


def report() -> list[dict[str, Any]]:
    """One row per provider for the dashboard's quota widget."""
    today = _today()
    with db.connect() as con:
        rows = dict(
            con.execute(
                "SELECT provider, calls FROM quota_usage WHERE usage_date = ?", [today]
            ).fetchall()
        )
    out = []
    for provider, limit in sorted(DAILY_LIMITS.items()):
        calls = int(rows.get(provider, 0))
        out.append(
            {
                "provider": provider,
                "used": calls,
                "limit": limit,
                "remaining": None if limit is None else max(0, limit - calls),
                "pct_used": None if limit is None else round(calls / limit * 100, 1),
                "hourly_limit": HOURLY_LIMITS.get(provider),
                "hourly_used": used_last_hour(provider) if HOURLY_LIMITS.get(provider) else None,
                "hourly_remaining": hourly_remaining(provider),
                "resets_at": reset_at(),
            }
        )
    return out
