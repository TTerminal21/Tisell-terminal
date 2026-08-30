"""Twelve Data - daily prices. Fallback when Tiingo and FMP are unavailable."""
from __future__ import annotations

from datetime import date
from typing import Any

from config import TWELVE_DATA_API_KEY
from sources import PRICES, NotConfigured, SourceError, price_row, to_date
from sources._http import get_json

NAME = "twelve_data"
CAPABILITIES = {PRICES}
BASE_URL = "https://api.twelvedata.com/time_series"
MAX_OUTPUT = 5000  # per-request cap on the free tier


def is_configured() -> bool:
    return bool(TWELVE_DATA_API_KEY)


def fetch_prices(
    ticker: str, start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    if not is_configured():
        raise NotConfigured("twelve_data: TWELVE_DATA_API_KEY is not set")

    params: dict[str, Any] = {
        "symbol": ticker.strip().upper(),
        "interval": "1day",
        "outputsize": MAX_OUTPUT,
        "format": "JSON",
        "apikey": TWELVE_DATA_API_KEY,
    }
    if start is not None:
        params["start_date"] = start.isoformat()
    if end is not None:
        params["end_date"] = end.isoformat()

    payload = get_json(NAME, BASE_URL, params=params)

    # Twelve Data signals errors in the body with status/code, not just HTTP.
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise SourceError(f"twelve_data: {payload.get('message', payload)}")

    values = (payload or {}).get("values") or []
    if not values:
        raise SourceError(f"twelve_data: no bars for {ticker.upper()} in that window")

    rows = [
        price_row(
            ticker, to_date(bar["datetime"]),
            open=bar.get("open"), high=bar.get("high"), low=bar.get("low"),
            close=bar.get("close"), volume=bar.get("volume"),
            adj_close=bar.get("close"),
        )
        for bar in values
        if to_date(bar.get("datetime")) is not None
    ]
    rows.sort(key=lambda row: row["date"])
    return rows
