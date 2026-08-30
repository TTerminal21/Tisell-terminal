"""Fetch through the provider chain and store the result in DuckDB.

Every function here returns which provider actually answered, so a caller can
tell whether it got the primary source or a fallback.

    python data-layer/ingest.py prices AAPL --start 2020-01-01
    python data-layer/ingest.py fundamentals AAPL --period quarterly
    python data-layer/ingest.py macro DGS10
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from typing import Any

import db
import registry
from sources import FUNDAMENTALS, MACRO, PRICES, PROFILE, SourceError

PRICE_COLUMNS = [
    "ticker", "date", "open", "high", "low", "close", "volume",
    "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume",
    "div_cash", "split_factor", "source", "fetched_at",
]
FUNDAMENTAL_COLUMNS = [
    "ticker", "period_end", "period_type", "statement", "metric",
    "value", "currency", "ordinal", "source", "fetched_at",
]
PROFILE_COLUMNS = [
    "ticker", "name", "exchange", "currency", "sector", "industry",
    "country", "cik", "market_cap", "beta", "description", "source", "fetched_at",
]
MACRO_SERIES_COLUMNS = [
    "series_id", "title", "units", "frequency", "seasonal", "source", "fetched_at",
]
MACRO_OBS_COLUMNS = ["series_id", "date", "value", "source", "fetched_at"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tuples(rows: list[dict[str, Any]], columns: list[str], source: str) -> list[tuple]:
    stamp = _now()
    return [
        tuple(row.get(column) for column in columns[:-2]) + (source, stamp)
        for row in rows
    ]


def ingest_prices(
    ticker: str, start: date | None = None, end: date | None = None,
    only: str | None = None,
) -> dict[str, Any]:
    provider, rows = registry.fetch(
        PRICES, ticker.strip().upper(), only=only, start=start, end=end
    )
    with db.write_lock(), db.connect() as con:
        stored = db.upsert(
            con, "prices", ["ticker", "date"], PRICE_COLUMNS,
            _tuples(rows, PRICE_COLUMNS, provider),
        )
    return {
        "capability": PRICES, "target": ticker.upper(), "provider": provider,
        "rows_stored": stored,
        "first_date": rows[0]["date"].isoformat(),
        "last_date": rows[-1]["date"].isoformat(),
    }


def ingest_fundamentals(
    ticker: str, period_type: str = "annual", limit: int = 8, only: str | None = None,
) -> dict[str, Any]:
    provider, rows = registry.fetch(
        FUNDAMENTALS, ticker.strip().upper(), only=only,
        period_type=period_type, limit=limit,
    )
    with db.write_lock(), db.connect() as con:
        stored = db.upsert(
            con, "fundamentals",
            ["ticker", "period_end", "period_type", "statement", "metric"],
            FUNDAMENTAL_COLUMNS, _tuples(rows, FUNDAMENTAL_COLUMNS, provider),
        )
    periods = sorted({row["period_end"] for row in rows})
    return {
        "capability": FUNDAMENTALS, "target": ticker.upper(), "provider": provider,
        "rows_stored": stored, "period_type": period_type,
        "periods": [p.isoformat() for p in periods],
    }


def ingest_profile(ticker: str, only: str | None = None) -> dict[str, Any]:
    provider, record = registry.fetch(PROFILE, ticker.strip().upper(), only=only)
    with db.write_lock(), db.connect() as con:
        stored = db.upsert(
            con, "profiles", ["ticker"], PROFILE_COLUMNS,
            _tuples([record], PROFILE_COLUMNS, provider),
        )
    return {
        "capability": PROFILE, "target": ticker.upper(), "provider": provider,
        "rows_stored": stored, "name": record.get("name"),
    }


def ingest_macro(
    series_id: str, start: date | None = None, only: str | None = None
) -> dict[str, Any]:
    provider, (meta, observations) = registry.fetch(
        MACRO, series_id.strip().upper(), only=only, start=start,
        row_count=lambda result: len(result[1]),
    )
    with db.write_lock(), db.connect() as con:
        db.upsert(
            con, "macro_series", ["series_id"], MACRO_SERIES_COLUMNS,
            _tuples([meta], MACRO_SERIES_COLUMNS, provider),
        )
        stored = db.upsert(
            con, "macro_observations", ["series_id", "date"], MACRO_OBS_COLUMNS,
            _tuples(observations, MACRO_OBS_COLUMNS, provider),
        )
    return {
        "capability": MACRO, "target": series_id.upper(), "provider": provider,
        "rows_stored": stored, "title": meta.get("title"), "units": meta.get("units"),
        "first_date": observations[0]["date"].isoformat(),
        "last_date": observations[-1]["date"].isoformat(),
    }


def _parse_date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capability", choices=["prices", "fundamentals", "profile", "macro"])
    parser.add_argument("target", help="ticker, or FRED series id for macro")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    parser.add_argument("--period", default="annual", choices=["annual", "quarterly"])
    parser.add_argument("--limit", type=int, default=8, help="periods to keep")
    parser.add_argument("--only", help="pin to one provider instead of the chain")
    args = parser.parse_args()

    try:
        if args.capability == "prices":
            result = ingest_prices(args.target, _parse_date(args.start),
                                   _parse_date(args.end), only=args.only)
        elif args.capability == "fundamentals":
            result = ingest_fundamentals(args.target, args.period, args.limit, only=args.only)
        elif args.capability == "profile":
            result = ingest_profile(args.target, only=args.only)
        else:
            result = ingest_macro(args.target, _parse_date(args.start), only=args.only)
    except SourceError as exc:
        print(f"error: {exc}")
        return 1

    print(f"{result['target']}: {result['rows_stored']} rows via {result['provider']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
