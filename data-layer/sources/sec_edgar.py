"""SEC EDGAR - as-filed XBRL company facts. The ground-truth US source.

No API key, but the SEC's fair-access policy requires a descriptive
User-Agent naming a real contact, or requests are refused with 403. Set
SEC_EDGAR_USER_AGENT in .env, e.g. "Jane Doe jane@example.com".

Values come straight from the filings, so this is the source to reconcile
against when two commercial providers disagree.
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from config import SEC_EDGAR_USER_AGENT
from sources import (
    FUNDAMENTALS, NotConfigured, SourceError, fundamental_row, to_date, to_float,
)
from sources._http import get_json

NAME = "sec_edgar"
CAPABILITIES = {FUNDAMENTALS}
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# 10-K is the annual filing, 10-Q the quarterly; their amendments count too.
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

_ticker_to_cik: dict[str, str] = {}
_map_lock = threading.Lock()


def is_configured() -> bool:
    return bool(SEC_EDGAR_USER_AGENT)


def _headers() -> dict[str, str]:
    if not is_configured():
        raise NotConfigured(
            "sec_edgar: SEC_EDGAR_USER_AGENT is not set - the SEC requires a "
            'descriptive contact, e.g. "Jane Doe jane@example.com"'
        )
    return {"User-Agent": SEC_EDGAR_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def cik_for(ticker: str) -> str:
    """Resolve a ticker to a zero-padded 10-digit CIK."""
    symbol = ticker.strip().upper()
    with _map_lock:
        if not _ticker_to_cik:
            payload = get_json(NAME, TICKER_MAP_URL, headers=_headers())
            for entry in (payload or {}).values():
                code = str(entry.get("ticker", "")).upper()
                if code:
                    _ticker_to_cik[code] = str(entry["cik_str"]).zfill(10)
        cik = _ticker_to_cik.get(symbol)
    if not cik:
        raise SourceError(f"sec_edgar: no CIK for {symbol} (US registrants only)")
    return cik


def fetch_fundamentals(
    ticker: str, period_type: str = "annual", limit: int = 8
) -> list[dict[str, Any]]:
    symbol = ticker.strip().upper()
    payload = get_json(
        NAME, FACTS_URL.format(cik=cik_for(symbol)), headers=_headers()
    )
    facts = (payload or {}).get("facts", {})
    if not facts:
        raise SourceError(f"sec_edgar: no XBRL facts for {symbol}")

    wanted_forms = _ANNUAL_FORMS if period_type == "annual" else _QUARTERLY_FORMS

    # One value per (metric, period end); later filings restate earlier ones, so
    # keep the most recently filed figure for any given period.
    best: dict[tuple[str, date], tuple[str, float, str | None]] = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        for metric, detail in (facts.get(taxonomy) or {}).items():
            for unit, entries in (detail.get("units") or {}).items():
                for entry in entries:
                    if entry.get("form") not in wanted_forms:
                        continue
                    period_end = to_date(entry.get("end"))
                    value = to_float(entry.get("val"))
                    if period_end is None or value is None:
                        continue
                    filed = str(entry.get("filed") or "")
                    key = (metric, period_end)
                    if key not in best or filed > best[key][0]:
                        best[key] = (filed, value, unit)

    if not best:
        raise SourceError(
            f"sec_edgar: no {period_type} facts for {symbol} in forms {sorted(wanted_forms)}"
        )

    # Keep only the most recent `limit` period ends, matching the other providers.
    period_ends = sorted({period_end for _, period_end in best}, reverse=True)[:limit]
    keep = set(period_ends)

    return [
        fundamental_row(
            symbol, period_end, period_type, "as_filed", metric, value,
            unit if unit in ("USD", "EUR", "GBP", "JPY", "CAD", "MXN") else None,
        )
        for (metric, period_end), (_filed, value, unit) in best.items()
        if period_end in keep
    ]
