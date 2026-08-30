"""Financial Modeling Prep - fundamentals, ratios, prices, profile.

Uses the `stable` endpoints. FMP retired the `/api/v3` family for accounts
created after 31 Aug 2025, so v3 returns 403 on a current free key.

Free tier is 250 calls/day, and a full fundamentals pull spends five of them
per ticker, which is what makes the watchlist size in refresh.py matter.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from config import FMP_API_KEY
from sources import (  # noqa: F401
    has_exchange_suffix,
    FUNDAMENTALS, PRICES, PROFILE, NotConfigured, SourceError,
    fundamental_row, price_row, to_date, to_float,
)
from sources._http import get_json

NAME = "fmp"
CAPABILITIES = {PRICES, FUNDAMENTALS, PROFILE}
BASE_URL = "https://financialmodelingprep.com/stable"

# Columns that are labels rather than figures; everything else is a metric.
_SKIP_FIELDS = {
    "symbol", "date", "reportedCurrency", "cik", "filingDate", "acceptedDate",
    "fiscalYear", "period", "calendarYear", "link", "finalLink",
}

# The free tier rejects limit > 5 with HTTP 402, which would otherwise 402 on
# every statement and silently demote FMP to a fallback. Clamp instead.
MAX_FREE_LIMIT = 5

_STATEMENTS = {
    "income": "income-statement",
    "balance": "balance-sheet-statement",
    "cash_flow": "cash-flow-statement",
    "ratios": "ratios",
    "metrics": "key-metrics",
}


def is_configured() -> bool:
    return bool(FMP_API_KEY)


def supports(ticker: str) -> bool:
    """The free tier answers 402 for non-US listings, so decline them up front."""
    return not has_exchange_suffix(ticker)


def _get(path: str, params: dict[str, Any]) -> Any:
    if not is_configured():
        raise NotConfigured("fmp: FMP_API_KEY is not set")
    payload = get_json(NAME, f"{BASE_URL}/{path}", params={**params, "apikey": FMP_API_KEY})
    if isinstance(payload, dict) and payload.get("Error Message"):
        raise SourceError(f"fmp: {payload['Error Message'][:200]}")
    return payload


def fetch_prices(
    ticker: str, start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"symbol": ticker.strip().upper()}
    if start is not None:
        params["from"] = start.isoformat()
    if end is not None:
        params["to"] = end.isoformat()

    payload = _get("historical-price-eod/full", params)
    if not payload:
        raise SourceError(f"fmp: no bars for {ticker.upper()} in that window")

    rows = [
        price_row(
            ticker, to_date(bar["date"]),
            open=bar.get("open"), high=bar.get("high"), low=bar.get("low"),
            close=bar.get("close"), volume=bar.get("volume"),
            # FMP's EOD series is already split/dividend adjusted.
            adj_close=bar.get("adjClose", bar.get("close")),
        )
        for bar in payload
        if to_date(bar.get("date")) is not None
    ]
    rows.sort(key=lambda row: row["date"])
    return rows


def fetch_fundamentals(
    ticker: str, period_type: str = "annual", limit: int = 8
) -> list[dict[str, Any]]:
    """Pull all five statements and flatten them into long metric rows."""
    symbol = ticker.strip().upper()
    period = "annual" if period_type == "annual" else "quarter"
    limit = min(limit, MAX_FREE_LIMIT)

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for statement, path in _STATEMENTS.items():
        try:
            payload = _get(path, {"symbol": symbol, "period": period, "limit": limit})
        except SourceError as exc:
            # One statement missing should not lose the other four.
            failures.append(f"{statement}: {exc}")
            continue
        for record in payload or []:
            period_end = to_date(record.get("date"))
            if period_end is None:
                continue
            currency = record.get("reportedCurrency")
            # FMP returns statement fields in presentation order and dicts
            # preserve it, so the field's position is the line's position.
            for position, (key, value) in enumerate(record.items()):
                if key in _SKIP_FIELDS or to_float(value) is None:
                    continue
                rows.append(
                    fundamental_row(
                        symbol, period_end, period_type, statement, key, value,
                        currency, position,
                    )
                )

    if not rows:
        raise SourceError(f"fmp: no fundamentals for {symbol} ({'; '.join(failures)})")
    return rows


def fetch_profile(ticker: str) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    payload = _get("profile", {"symbol": symbol})
    if not payload:
        raise SourceError(f"fmp: no profile for {symbol}")
    record = payload[0]
    return {
        "ticker": symbol,
        "name": record.get("companyName"),
        "exchange": record.get("exchange") or record.get("exchangeFullName"),
        "currency": record.get("currency"),
        "sector": record.get("sector"),
        "industry": record.get("industry"),
        "country": record.get("country"),
        "cik": record.get("cik"),
        "market_cap": to_float(record.get("marketCap")),
        "beta": to_float(record.get("beta")),
        "description": (record.get("description") or "")[:2000] or None,
    }
