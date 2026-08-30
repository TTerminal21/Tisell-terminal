"""Alpaca Market Data - daily bars. Fallback price source.

Alpaca pages its bar responses; this follows next_page_token to completion.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from config import ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY
from sources import PRICES, NotConfigured, SourceError, price_row, to_date
from sources._http import get_json

NAME = "alpaca"
CAPABILITIES = {PRICES}
BASE_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
PAGE_LIMIT = 10000
MAX_PAGES = 50  # guard against an unbounded paging loop


def is_configured() -> bool:
    return bool(ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY)


def fetch_prices(
    ticker: str, start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    if not is_configured():
        raise NotConfigured(
            "alpaca: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set"
        )

    symbol = ticker.strip().upper()
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY_ID,
        "APCA-API-SECRET-KEY": ALPACA_API_SECRET_KEY,
    }
    params: dict[str, Any] = {
        "timeframe": "1Day",
        "limit": PAGE_LIMIT,
        "adjustment": "all",
        "feed": "iex",  # the feed available on a free Alpaca account
        "start": (start or date(2016, 1, 1)).isoformat(),
    }
    if end is not None:
        params["end"] = end.isoformat()

    rows: list[dict[str, Any]] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        page_params = dict(params)
        if page_token:
            page_params["page_token"] = page_token

        payload = get_json(
            NAME, BASE_URL.format(symbol=symbol), params=page_params, headers=headers
        )
        for bar in (payload or {}).get("bars") or []:
            day = to_date(bar.get("t"))
            if day is None:
                continue
            rows.append(
                price_row(
                    ticker, day,
                    open=bar.get("o"), high=bar.get("h"), low=bar.get("l"),
                    close=bar.get("c"), volume=bar.get("v"),
                    adj_close=bar.get("c"),
                )
            )
        page_token = (payload or {}).get("next_page_token")
        if not page_token:
            break

    if not rows:
        raise SourceError(f"alpaca: no bars for {symbol} in that window")
    rows.sort(key=lambda row: row["date"])
    return rows
