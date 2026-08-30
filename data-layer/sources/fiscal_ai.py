"""Fiscal.ai - standardized fundamentals, segment/KPI data, profile.

Companies are addressed by a companyKey of the form EXCHANGE_TICKER for US/CA
listings. A bare ticker is resolved against the company list, which is cached
for the process lifetime because it is a large and rarely-changing document.
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from config import FISCAL_AI_API_KEY
from sources import (
    FUNDAMENTALS, PROFILE, NotConfigured, SourceError,
    fundamental_row, has_exchange_suffix, to_date, to_float,
)
from sources._http import get_json

NAME = "fiscal_ai"
CAPABILITIES = {FUNDAMENTALS, PROFILE}
BASE_URL = "https://api.fiscal.ai"

_STATEMENT_PATHS = {
    "income": "income-statement",
    "balance": "balance-sheet",
    "cash_flow": "cash-flow-statement",
}

# US/CA listings are the common case; try these before paying for a full lookup.
_COMMON_EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "TSX")

_company_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


def is_configured() -> bool:
    return bool(FISCAL_AI_API_KEY)


def supports(ticker: str) -> bool:
    """Decline non-US listings up front.

    company_key() probes four exchanges to resolve a bare ticker, so a symbol
    it can never resolve costs four calls rather than one. Sixty BMV names in
    a profile pass is 240 wasted calls against a 250/day tier - which is
    exactly how this provider went over budget before the check existed.
    """
    return not has_exchange_suffix(ticker)


def _get(path: str, params: dict[str, Any]) -> Any:
    if not is_configured():
        raise NotConfigured("fiscal_ai: FISCAL_AI_API_KEY is not set")
    return get_json(
        NAME, f"{BASE_URL}{path}", params={**params, "apiKey": FISCAL_AI_API_KEY}
    )


def company_key(ticker: str) -> str:
    """Resolve a bare ticker to Fiscal.ai's EXCHANGE_TICKER key."""
    symbol = ticker.strip().upper()
    if "_" in symbol:
        return symbol

    with _cache_lock:
        if symbol in _company_cache:
            return _company_cache[symbol]

    for exchange in _COMMON_EXCHANGES:
        candidate = f"{exchange}_{symbol}"
        try:
            _get("/v3/company/profile", {"companyKey": candidate})
        except SourceError:
            continue
        with _cache_lock:
            _company_cache[symbol] = candidate
        return candidate

    raise SourceError(f"fiscal_ai: could not resolve a companyKey for {symbol}")


def fetch_profile(ticker: str) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    record = _get("/v3/company/profile", {"companyKey": company_key(symbol)})
    if not record:
        raise SourceError(f"fiscal_ai: no profile for {symbol}")
    primary = record.get("primaryListing") or {}
    return {
        "ticker": symbol,
        "name": record.get("displayNameEnglish") or record.get("legalNameEnglish"),
        "exchange": primary.get("exchangeCode") or primary.get("exchangeName"),
        "currency": primary.get("currency") or record.get("reportingCurrency"),
        "sector": record.get("sector"),
        "industry": record.get("industry"),
        "country": record.get("headquartersCountryCode"),
        "cik": str(record["cik"]) if record.get("cik") else None,
        "market_cap": to_float(record.get("marketCapUsd")),
        "beta": None,
        "description": (record.get("descriptionLong")
                        or record.get("descriptionShort") or "")[:2000] or None,
    }


def fetch_fundamentals(
    ticker: str, period_type: str = "annual", limit: int = 8
) -> list[dict[str, Any]]:
    symbol = ticker.strip().upper()
    key = company_key(symbol)
    wanted = "Annual" if period_type == "annual" else "Quarterly"

    rows: list[dict[str, Any]] = []
    for statement, path in _STATEMENT_PATHS.items():
        try:
            payload = _get(
                f"/v1/company/financials/{path}/standardized", {"companyKey": key}
            )
        except SourceError:
            continue

        periods = [
            entry for entry in (payload.get("data") or [])
            if entry.get("periodType") == wanted and to_date(entry.get("reportDate"))
        ]
        # Provider order is not guaranteed, so sort rather than slice on faith.
        periods.sort(key=lambda entry: to_date(entry["reportDate"]))
        for entry in periods[-limit:]:
            period_end = to_date(entry.get("reportDate"))
            if period_end is None:
                continue
            for position, (metric, detail) in enumerate(
                (entry.get("metricsValues") or {}).items()
            ):
                if not isinstance(detail, dict):
                    continue
                if to_float(detail.get("value")) is None:
                    continue
                rows.append(
                    fundamental_row(
                        symbol, period_end, period_type, statement, metric,
                        detail.get("value"), detail.get("currency"), position,
                    )
                )

    if not rows:
        raise SourceError(f"fiscal_ai: no fundamentals for {symbol}")
    return rows


def fetch_segments(ticker: str, period_type: str = "annual") -> list[dict[str, Any]]:
    """Segment and KPI series - the thing Fiscal.ai has that the others do not."""
    symbol = ticker.strip().upper()
    payload = _get("/v2/company/segments-and-kpis", {"companyKey": company_key(symbol)})
    wanted = "Annual" if period_type == "annual" else "Quarterly"

    names = {
        m.get("metricId"): m.get("metricName")
        for m in (payload.get("metrics") or [])
    }
    rows: list[dict[str, Any]] = []
    for entry in payload.get("data") or []:
        if entry.get("periodType") != wanted:
            continue
        period_end = to_date(entry.get("reportDate"))
        if period_end is None:
            continue
        for metric_id, detail in (entry.get("metricsValues") or {}).items():
            value = detail.get("value") if isinstance(detail, dict) else detail
            if to_float(value) is None:
                continue
            rows.append(
                fundamental_row(
                    symbol, period_end, period_type, "segments",
                    names.get(metric_id) or metric_id, value,
                    detail.get("currency") if isinstance(detail, dict) else None,
                )
            )
    return rows
