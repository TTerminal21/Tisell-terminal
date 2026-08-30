"""yfinance - broad free coverage, unofficial and scraped.

Deliberately last in every chain it appears in. It needs no key, so it is the
provider that still answers when every metered tier is exhausted, but its
shape changes without notice and it is treated as a fallback, never a
reference. Calls are recorded against quota for visibility even though there
is no published limit.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sources import (
    FUNDAMENTALS, PRICES, PROFILE, SourceError,
    fundamental_row, price_row, to_date, to_float,
)

NAME = "yfinance"
CAPABILITIES = {PRICES, FUNDAMENTALS, PROFILE}

_STATEMENTS = ("income", "balance", "cash_flow")


def is_configured() -> bool:
    return True  # no credentials required


def _ticker(symbol: str):
    import quota
    import yfinance

    quota.record(NAME)
    return yfinance.Ticker(symbol.strip().upper())


def fetch_prices(
    ticker: str, start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    try:
        frame = _ticker(ticker).history(
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            period=None if start else "max",
            auto_adjust=False,
            actions=True,
            raise_errors=True,
        )
    except Exception as exc:  # yfinance raises a wide variety of its own errors
        raise SourceError(f"yfinance: {type(exc).__name__}: {exc}") from exc

    if frame is None or frame.empty:
        raise SourceError(f"yfinance: no bars for {ticker.upper()} in that window")

    def cell(row: Any, *names: str) -> Any:
        for name in names:
            if name in row.index:
                return row[name]
        return None

    rows = []
    for stamp, row in frame.iterrows():
        day = to_date(getattr(stamp, "date", lambda: stamp)())
        if day is None:
            continue
        adj_close = to_float(cell(row, "Adj Close"))
        rows.append(
            price_row(
                ticker, day,
                open=cell(row, "Open"), high=cell(row, "High"),
                low=cell(row, "Low"), close=cell(row, "Close"),
                volume=cell(row, "Volume"),
                adj_close=adj_close if adj_close is not None else to_float(cell(row, "Close")),
                div_cash=cell(row, "Dividends"),
                split_factor=cell(row, "Stock Splits"),
            )
        )
    rows.sort(key=lambda row: row["date"])
    return rows


def fetch_profile(ticker: str) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    try:
        info = _ticker(symbol).info or {}
    except Exception as exc:
        raise SourceError(f"yfinance: {type(exc).__name__}: {exc}") from exc
    if not info.get("shortName") and not info.get("longName"):
        raise SourceError(f"yfinance: no profile for {symbol}")
    return {
        "ticker": symbol,
        "name": info.get("longName") or info.get("shortName"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "currency": info.get("currency"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "cik": None,
        "market_cap": to_float(info.get("marketCap")),
        "beta": to_float(info.get("beta")),
        "description": (info.get("longBusinessSummary") or "")[:2000] or None,
    }


def fetch_fundamentals(
    ticker: str, period_type: str = "annual", limit: int = 8
) -> list[dict[str, Any]]:
    symbol = ticker.strip().upper()
    handle = _ticker(symbol)
    quarterly = period_type != "annual"

    frames = {
        "income": handle.quarterly_income_stmt if quarterly else handle.income_stmt,
        "balance": handle.quarterly_balance_sheet if quarterly else handle.balance_sheet,
        "cash_flow": handle.quarterly_cashflow if quarterly else handle.cashflow,
    }

    rows: list[dict[str, Any]] = []
    for statement in _STATEMENTS:
        frame = frames.get(statement)
        if frame is None or getattr(frame, "empty", True):
            continue
        # Columns are period-end dates, rows are line items.
        for column in list(frame.columns)[:limit]:
            period_end = to_date(getattr(column, "date", lambda: column)())
            if period_end is None:
                continue
            for position, (metric, value) in enumerate(frame[column].items()):
                if to_float(value) is None:
                    continue
                rows.append(
                    fundamental_row(
                        symbol, period_end, period_type, statement, str(metric),
                        value, None, position,
                    )
                )

    if not rows:
        raise SourceError(f"yfinance: no fundamentals for {symbol}")
    return rows
