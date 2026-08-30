"""Symbol lookup - turn a company name into a ticker.

Uses Yahoo's public search index, which is the same universe yfinance reads
from, so anything this returns is fetchable through the yfinance provider.
That matters for non-US listings: BMV names resolve here as EXCHANGE-suffixed
symbols (La Comer -> LACOMERUBC.MX) that Tiingo and FMP do not carry at all.

No credentials required.
"""
from __future__ import annotations

from typing import Any

from sources import SourceError
from sources._http import get_json

NAME = "symbol_search"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"

# Yahoo suffixes the exchange for non-US listings; spell out the ones that
# matter here so the picker is readable rather than cryptic.
EXCHANGE_LABELS = {
    "MEX": "BMV (Mexico)", "NMS": "NASDAQ", "NYQ": "NYSE", "PCX": "NYSE Arca",
    "PNK": "OTC", "TOR": "Toronto", "LSE": "London", "GER": "Xetra",
    "BUE": "Buenos Aires", "SAO": "São Paulo",
}


def is_configured() -> bool:
    return True


def search(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Best-effort symbol matches for a free-text company name or ticker."""
    text = query.strip()
    if not text:
        return []

    payload = get_json(
        NAME, SEARCH_URL,
        params={"q": text, "quotesCount": limit, "newsCount": 0},
        # Yahoo's search rejects a default python-httpx agent.
        headers={"User-Agent": "Mozilla/5.0 (compatible; TisellTerminal/1.0)"},
    )

    results = []
    for item in (payload or {}).get("quotes", []):
        symbol = item.get("symbol")
        if not symbol:
            continue
        exchange = item.get("exchange") or ""
        results.append({
            "symbol": symbol,
            "name": item.get("shortname") or item.get("longname") or "",
            "exchange": exchange,
            "exchange_label": EXCHANGE_LABELS.get(exchange, exchange),
            "type": (item.get("quoteType") or "").upper(),
        })
    if not results:
        raise SourceError(f"no symbols matched {text!r}")
    return results
