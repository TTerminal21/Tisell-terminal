"""Data providers.

Every module here is one provider and follows the same contract, so the
fallback chain in registry.py can rotate through them without knowing which is
which:

    NAME             str                      - stable id, matches quota.DAILY_LIMITS
    CAPABILITIES     set[str]                 - any of: prices fundamentals profile macro
    is_configured()  -> bool                  - credentials present?
    fetch_prices(ticker, start, end)          -> list[price row]
    fetch_fundamentals(ticker, period_type)   -> list[fundamental row]
    fetch_profile(ticker)                     -> profile row
    fetch_macro(series_id, start)             -> (series meta, list[observation])

A provider implements only the capabilities it declares. Anything it cannot
answer raises SourceError, which the chain treats as "try the next one".
"""
from __future__ import annotations

from datetime import date
from typing import Any

PRICES = "prices"
FUNDAMENTALS = "fundamentals"
PROFILE = "profile"
MACRO = "macro"


class SourceError(RuntimeError):
    """A provider could not answer the request. The chain moves on."""


class NotConfigured(SourceError):
    """No credentials for this provider. Skipped without being counted."""


def has_exchange_suffix(ticker: str) -> bool:
    """True for symbols like LACOMERUBC.MX - a non-US listing.

    US-only providers use this to decline a symbol up front instead of
    spending a call to discover they do not carry it.
    """
    return "." in ticker.strip()


def to_float(value: Any) -> float | None:
    if value is None or value == "" or value == ".":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # NaN never round-trips through JSON or DuckDB cleanly; store it as missing.
    return None if result != result else result


def to_int(value: Any) -> int | None:
    parsed = to_float(value)
    return None if parsed is None else int(parsed)


def to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


_PRICE_INTS = ("volume", "adj_volume")


def price_row(ticker: str, day: date, **fields: Any) -> dict[str, Any]:
    """Normalised price row. Providers fill what they have; the rest stay None.

    Every numeric field is coerced here rather than trusted from the provider.
    yfinance in particular returns NaN on non-trading gap days, and NaN is not
    NULL to DuckDB - it stores fine and then serialises as a bare `NaN`, which
    is invalid JSON and breaks any client that reads it back.
    """
    row = {
        "ticker": ticker.upper(), "date": day,
        "open": None, "high": None, "low": None, "close": None, "volume": None,
        "adj_open": None, "adj_high": None, "adj_low": None, "adj_close": None,
        "adj_volume": None, "div_cash": None, "split_factor": None,
    }
    for key, value in fields.items():
        if key not in row:
            continue
        row[key] = to_int(value) if key in _PRICE_INTS else to_float(value)
    return row


def fundamental_row(ticker: str, period_end: date, period_type: str, statement: str,
                    metric: str, value: Any, currency: str | None = None,
                    ordinal: int | None = None) -> dict[str, Any]:
    """One stored figure.

    `ordinal` is the line's position within its statement as the filer
    presented it. Providers that expose an ordering (CNBV's presentation
    hierarchy, FMP's field order) should pass it; a statement without one
    falls back to alphabetical, which is wrong for a P&L but is all there is.
    """
    return {
        "ticker": ticker.upper(), "period_end": period_end,
        "period_type": period_type, "statement": statement,
        "metric": metric, "value": to_float(value), "currency": currency,
        "ordinal": ordinal,
    }
