"""Tiingo - daily prices. Official API, primary price source."""
from __future__ import annotations

from datetime import date
from typing import Any

from config import TIINGO_API_KEY
from sources import (
    PRICES, NotConfigured, SourceError, has_exchange_suffix, price_row, to_date,
)
from sources._http import get_json

NAME = "tiingo"
CAPABILITIES = {PRICES}
BASE_URL = "https://api.tiingo.com/tiingo/daily"

# Tiingo returns only the latest close when no startDate is sent, so an
# unqualified fetch must ask for everything explicitly. Tiingo clamps this to
# wherever its coverage of the ticker actually begins.
FULL_HISTORY_START = date(1900, 1, 1)


def is_configured() -> bool:
    return bool(TIINGO_API_KEY)


def supports(ticker: str) -> bool:
    """Tiingo's EOD coverage here is US listings; BMV symbols are not carried."""
    return not has_exchange_suffix(ticker)


def fetch_prices(
    ticker: str, start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    if not is_configured():
        raise NotConfigured("tiingo: TIINGO_API_KEY is not set")

    params: dict[str, str] = {
        "format": "json",
        "resampleFreq": "daily",
        "startDate": (start or FULL_HISTORY_START).isoformat(),
    }
    if end is not None:
        params["endDate"] = end.isoformat()

    # The token goes in the Authorization header, not the query string.
    payload = get_json(
        NAME,
        f"{BASE_URL}/{ticker.strip().lower()}/prices",
        params=params,
        headers={"Authorization": f"Token {TIINGO_API_KEY}",
                 "Content-Type": "application/json"},
    )

    if isinstance(payload, dict):
        # Tiingo reports some errors as 200 + {"detail": "..."}.
        raise SourceError(f"tiingo: {payload.get('detail') or payload}")
    if not payload:
        raise SourceError(f"tiingo: no bars for {ticker.upper()} in that window")

    rows = [
        price_row(
            ticker, to_date(bar["date"]),
            open=bar.get("open"), high=bar.get("high"), low=bar.get("low"),
            close=bar.get("close"), volume=bar.get("volume"),
            adj_open=bar.get("adjOpen"), adj_high=bar.get("adjHigh"),
            adj_low=bar.get("adjLow"), adj_close=bar.get("adjClose"),
            adj_volume=bar.get("adjVolume"), div_cash=bar.get("divCash"),
            split_factor=bar.get("splitFactor"),
        )
        for bar in payload
        if to_date(bar.get("date")) is not None
    ]
    rows.sort(key=lambda row: row["date"])
    return rows
