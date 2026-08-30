"""FastAPI data layer.

Run from the repo root:

    uvicorn --app-dir data-layer main:app --reload --port 8000

Read endpoints serve what is already in DuckDB and never call a provider.
Only /ingest and /refresh spend quota. The shared API-key header is v1 item 3
and is deliberately not here yet.
"""
from __future__ import annotations

import secrets
from datetime import date
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Path, Query, Request

import db
import ingest
import quota
import refresh as refresh_job
import registry
import watchlist
from sources import symbol_search
from config import API_KEY_HEADER, DATA_LAYER_API_KEY, DUCKDB_PATH
from sources import SourceError


# /health stays reachable so a client can tell "backend down" from
# "backend up but my key is wrong". The docs carry no data, so they stay open
# too. Everything else - all reads and all writes - needs the header.
OPEN_PATHS = frozenset(
    {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
)


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(None, alias=API_KEY_HEADER),
) -> None:
    """Shared-secret check.

    Worth keeping even on localhost: anything else on the machine can reach
    127.0.0.1, and the moment a piece runs on another device this is the only
    thing standing in front of the write endpoints. Blank key = check disabled,
    so an existing local setup is not broken by adding this.

    Applied app-wide. FastAPI does not let a route opt out of an app-level
    dependency with `dependencies=[]`, so the exemption lives here.
    """
    if not DATA_LAYER_API_KEY or request.url.path in OPEN_PATHS:
        return
    # compare_digest avoids leaking the key length through timing.
    if not x_api_key or not secrets.compare_digest(x_api_key, DATA_LAYER_API_KEY):
        raise HTTPException(401, f"Missing or invalid {API_KEY_HEADER} header")


app = FastAPI(
    title="Tisell Terminal - data layer",
    version="0.3.0",
    summary="Local price/fundamentals/macro store over DuckDB, with provider fallback.",
    dependencies=[Depends(require_api_key)],
)


def _rows(sql: str, params: list[Any] | None = None) -> list[tuple]:
    with db.connect() as con:
        return con.execute(sql, params or []).fetchall()


# --- Status ---------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    counts = {}
    with db.connect() as con:
        for table in ("prices", "fundamentals", "profiles", "macro_observations"):
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        counts["tickers"] = con.execute(
            "SELECT COUNT(DISTINCT ticker) FROM prices"
        ).fetchone()[0]
        counts["macro_series"] = con.execute(
            "SELECT COUNT(*) FROM macro_series"
        ).fetchone()[0]
    return {"status": "ok", "duckdb_path": str(DUCKDB_PATH), **counts}


@app.get("/providers")
def providers() -> dict[str, Any]:
    """Which providers are configured, and the fallback order per capability."""
    return {
        "providers": registry.status(),
        "chains": {
            capability: [module.NAME for module in registry.providers_for(capability)]
            for capability in ("prices", "fundamentals", "profile", "macro")
        },
    }


@app.get("/quota")
def quota_report() -> dict[str, Any]:
    """Calls used per provider today, against known free-tier limits."""
    return {"providers": quota.report(), "resets_at": quota.reset_at()}


@app.get("/logs")
def logs(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    rows = _rows(
        """
        SELECT run_at, capability, target, provider, status, rows, message
        FROM fetch_log ORDER BY run_at DESC LIMIT ?
        """,
        [limit],
    )
    return {
        "entries": [
            {
                "run_at": run_at.isoformat(), "capability": capability,
                "target": target, "provider": provider, "status": status,
                "rows": row_count, "message": message,
            }
            for run_at, capability, target, provider, status, row_count, message in rows
        ]
    }


@app.get("/search")
def search_symbols(
    q: str = Query(..., min_length=1, description="company name or ticker"),
    limit: int = Query(12, ge=1, le=25),
) -> dict[str, Any]:
    """Resolve a company name to tickers.

    Non-US listings carry an exchange suffix (La Comer is LACOMERUBC.MX) and
    are served by yfinance - Tiingo and FMP have no BMV coverage.
    """
    try:
        return {"query": q, "results": symbol_search.search(q, limit)}
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc


# --- Watchlist ------------------------------------------------------------

@app.get("/watchlist")
def get_watchlist() -> dict[str, Any]:
    return {**watchlist.load(), "estimated_calls": watchlist.estimated_daily_calls()}


@app.put("/watchlist")
def put_watchlist(payload: dict[str, list[str]] = Body(...)) -> dict[str, Any]:
    """Replace the watchlist wholesale."""
    return watchlist.save(payload)


@app.post("/watchlist/{kind}")
def add_to_watchlist(
    kind: str = Path(..., pattern="^(equities|mexico|etfs|macro)$"),
    items: list[str] = Body(..., embed=True),
) -> dict[str, Any]:
    return watchlist.add(kind, items)


@app.delete("/watchlist/{kind}")
def remove_from_watchlist(
    kind: str = Path(..., pattern="^(equities|mexico|etfs|macro)$"),
    items: list[str] = Query(...),
) -> dict[str, Any]:
    return watchlist.remove(kind, items)


# --- Reads ----------------------------------------------------------------

@app.get("/tickers")
def list_tickers() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT p.ticker, COUNT(*) AS bars, MIN(p.date), MAX(p.date),
               MAX(p.source), ANY_VALUE(pr.name), ANY_VALUE(pr.sector)
        FROM prices p LEFT JOIN profiles pr ON pr.ticker = p.ticker
        GROUP BY p.ticker ORDER BY p.ticker
        """
    )
    return {
        "tickers": [
            {
                "ticker": ticker, "bars": bars,
                "first_date": first.isoformat(), "last_date": last.isoformat(),
                "source": source, "name": name, "sector": sector,
            }
            for ticker, bars, first, last, source, name, sector in rows
        ]
    }


@app.get("/prices/{ticker}")
def get_prices(
    ticker: str = Path(...),
    start: date | None = Query(None),
    end: date | None = Query(None),
) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    clauses, params = ["ticker = ?"], [symbol]
    if start is not None:
        clauses.append("date >= ?")
        params.append(start)
    if end is not None:
        clauses.append("date <= ?")
        params.append(end)

    rows = _rows(
        f"""
        SELECT date, open, high, low, close, volume, adj_close, source
        FROM prices WHERE {' AND '.join(clauses)} ORDER BY date
        """,
        params,
    )
    if not rows:
        raise HTTPException(
            404,
            f"No stored bars for {symbol}"
            + (" in that window" if start or end else "")
            + f". Fetch it first: POST /ingest/prices/{symbol}",
        )
    bars = [
        {"date": d.isoformat(), "open": o, "high": h, "low": low,
         "close": c, "volume": v, "adj_close": adj}
        for d, o, h, low, c, v, adj, _ in rows
    ]
    return {
        "ticker": symbol, "source": rows[0][-1], "count": len(bars),
        "start": bars[0]["date"], "end": bars[-1]["date"], "bars": bars,
    }


@app.get("/fundamentals/{ticker}")
def get_fundamentals(
    ticker: str = Path(...),
    period_type: str = Query("annual", pattern="^(annual|quarterly)$"),
    statement: str | None = Query(None),
    metric: str | None = Query(None),
) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    clauses, params = ["ticker = ?", "period_type = ?"], [symbol, period_type]
    if statement:
        clauses.append("statement = ?")
        params.append(statement)
    if metric:
        clauses.append("metric ILIKE ?")
        params.append(f"%{metric}%")

    rows = _rows(
        f"""
        SELECT period_end, statement, metric, value, currency, source, ordinal
        FROM fundamentals WHERE {' AND '.join(clauses)}
        -- Filed order first; alphabetical only where a source gave us none.
        ORDER BY period_end DESC, statement, ordinal NULLS LAST, metric
        """,
        params,
    )
    if not rows:
        raise HTTPException(
            404,
            f"No stored {period_type} fundamentals for {symbol}. "
            f"Fetch them first: POST /ingest/fundamentals/{symbol}",
        )
    return {
        "ticker": symbol, "period_type": period_type, "count": len(rows),
        "sources": sorted({r[5] for r in rows}),
        "periods": sorted({r[0].isoformat() for r in rows}, reverse=True),
        "items": [
            {"period_end": p.isoformat(), "statement": s, "metric": m,
             "value": v, "currency": c, "source": src, "ordinal": o}
            for p, s, m, v, c, src, o in rows
        ],
    }


@app.get("/profile/{ticker}")
def get_profile(ticker: str = Path(...)) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    rows = _rows(
        """
        SELECT ticker, name, exchange, currency, sector, industry, country,
               cik, market_cap, beta, description, source, fetched_at
        FROM profiles WHERE ticker = ?
        """,
        [symbol],
    )
    if not rows:
        raise HTTPException(
            404, f"No stored profile for {symbol}. POST /ingest/profile/{symbol}"
        )
    keys = ["ticker", "name", "exchange", "currency", "sector", "industry",
            "country", "cik", "market_cap", "beta", "description", "source"]
    record = dict(zip(keys, rows[0][:-1]))
    record["fetched_at"] = rows[0][-1].isoformat()
    return record


@app.get("/macro")
def list_macro() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT s.series_id, s.title, s.units, s.frequency, COUNT(o.date),
               MIN(o.date), MAX(o.date)
        FROM macro_series s LEFT JOIN macro_observations o ON o.series_id = s.series_id
        GROUP BY s.series_id, s.title, s.units, s.frequency ORDER BY s.series_id
        """
    )
    return {
        "series": [
            {"series_id": sid, "title": title, "units": units, "frequency": freq,
             "observations": n,
             "first_date": first.isoformat() if first else None,
             "last_date": last.isoformat() if last else None}
            for sid, title, units, freq, n, first, last in rows
        ]
    }


@app.get("/macro/{series_id}")
def get_macro(
    series_id: str = Path(...),
    start: date | None = Query(None),
    end: date | None = Query(None),
) -> dict[str, Any]:
    sid = series_id.strip().upper()
    clauses, params = ["o.series_id = ?"], [sid]
    if start is not None:
        clauses.append("o.date >= ?")
        params.append(start)
    if end is not None:
        clauses.append("o.date <= ?")
        params.append(end)

    rows = _rows(
        f"""
        SELECT o.date, o.value, s.title, s.units, o.source
        FROM macro_observations o
        LEFT JOIN macro_series s ON s.series_id = o.series_id
        WHERE {' AND '.join(clauses)} ORDER BY o.date
        """,
        params,
    )
    if not rows:
        raise HTTPException(
            404, f"No stored observations for {sid}. POST /ingest/macro/{sid}"
        )
    return {
        "series_id": sid, "title": rows[0][2], "units": rows[0][3],
        "source": rows[0][4], "count": len(rows),
        "observations": [{"date": d.isoformat(), "value": v} for d, v, _, _, _ in rows],
    }


# --- Writes ---------------------------------------------------------------

@app.post("/ingest/{capability}/{target}")
def ingest_target(
    capability: str = Path(..., pattern="^(prices|fundamentals|profile|macro)$"),
    target: str = Path(...),
    start: date | None = Query(None),
    end: date | None = Query(None),
    period_type: str = Query("annual", pattern="^(annual|quarterly)$"),
    only: str | None = Query(None, description="pin to one provider"),
) -> dict[str, Any]:
    """Fetch through the fallback chain and store."""
    try:
        if capability == "prices":
            return ingest.ingest_prices(target, start, end, only=only)
        if capability == "fundamentals":
            return ingest.ingest_fundamentals(target, period_type, only=only)
        if capability == "profile":
            return ingest.ingest_profile(target, only=only)
        return ingest.ingest_macro(target, start, only=only)
    except SourceError as exc:
        # 502: we are fine, every upstream provider was not.
        raise HTTPException(502, str(exc)) from exc


@app.post("/refresh")
def run_refresh(
    what: list[str] | None = Query(None),
    since: date | None = Query(None),
    period_type: str = Query("annual", pattern="^(annual|quarterly)$"),
    full_history: bool = Query(False),
    limit: int | None = Query(None, ge=1, description="cap targets per capability"),
) -> dict[str, Any]:
    """Refresh the whole watchlist. Slow by design; call it from a scheduler."""
    return refresh_job.refresh(
        what=what, since=since, period_type=period_type,
        full_history=full_history, limit=limit,
    )
