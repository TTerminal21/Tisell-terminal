"""Thin client for the FastAPI data layer.

Every dashboard page goes through here, so the TUI (item 5) can reuse the same
endpoints without inheriting any Streamlit assumptions.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# The analytics package lives at the repo root so the TUI can import it too.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BASE_URL = (os.getenv("DATA_LAYER_URL") or "http://127.0.0.1:8000").rstrip("/")
API_KEY = (os.getenv("DATA_LAYER_API_KEY") or "").strip()


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text[:300]


def get(path: str, params: dict[str, Any] | None = None,
        timeout: float = 60.0) -> tuple[Any, str | None]:
    """Return (payload, error). Errors are surfaced in the UI, never raised."""
    try:
        response = httpx.get(f"{BASE_URL}{path}", params=params,
                             headers=_headers(), timeout=timeout)
    except httpx.HTTPError as exc:
        return None, f"Cannot reach the data layer at {BASE_URL} — is it running? ({exc})"
    if response.status_code >= 400:
        return None, _detail(response)
    return response.json(), None


def post(path: str, params: dict[str, Any] | None = None,
         json: Any = None, timeout: float = 600.0) -> tuple[Any, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}{path}", params=params, json=json,
                              headers=_headers(), timeout=timeout)
    except httpx.HTTPError as exc:
        return None, f"Cannot reach the data layer at {BASE_URL} — is it running? ({exc})"
    if response.status_code >= 400:
        return None, _detail(response)
    return response.json(), None


@st.cache_data(ttl=300, show_spinner=False)
def cached_get(path: str, params: dict[str, Any] | None = None) -> tuple[Any, str | None]:
    """Reads hit DuckDB, not a provider, but caching keeps page switches snappy."""
    return get(path, params)


def require_backend() -> dict[str, Any]:
    """Every page calls this first; stops the page with instructions if down."""
    health, error = get("/health", timeout=10.0)
    if error:
        st.error(error)
        st.caption("Start the data layer from the repo root:")
        st.code("uvicorn --app-dir data-layer main:app --reload --port 8000", language="bash")
        st.stop()
    return health


def prices_frame(ticker: str, start: str | None = None) -> pd.DataFrame:
    """Stored daily bars as a DataFrame indexed by date."""
    params = {"start": start} if start else None
    payload, error = cached_get(f"/prices/{ticker}", params)
    if error or not payload:
        return pd.DataFrame()
    frame = pd.DataFrame(payload["bars"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date").sort_index()


def fundamentals_frame(ticker: str, period_type: str = "annual") -> pd.DataFrame:
    payload, error = cached_get(
        f"/fundamentals/{ticker}", {"period_type": period_type}
    )
    if error or not payload:
        return pd.DataFrame()
    return pd.DataFrame(payload["items"])


def statement_pivot(frame: pd.DataFrame, statement: str,
                    source: str | None = None) -> pd.DataFrame:
    """Long fundamentals -> metrics as rows, period ends as columns.

    Pinning to one source matters: providers store the same figure under
    different metric names, so mixing them double-counts line items.
    """
    if frame.empty:
        return pd.DataFrame()
    subset = frame[frame["statement"] == statement]
    if source:
        subset = subset[subset["source"] == source]
    if subset.empty:
        return pd.DataFrame()
    pivot = subset.pivot_table(
        index="metric", columns="period_end", values="value", aggfunc="first"
    )
    pivot = pivot[sorted(pivot.columns, reverse=True)]

    # pivot_table sorts the index alphabetically, which is meaningless for a
    # statement that reads top to bottom. Restore the order the filer used,
    # keeping any metric without an ordinal at the end.
    if "ordinal" in subset.columns:
        order = (
            subset.groupby("metric")["ordinal"].min()
            .reindex(pivot.index)
            .sort_values(na_position="last", kind="stable")
        )
        pivot = pivot.reindex(order.index)
    return pivot


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Multi-sheet workbook for st.download_button. Reused by item 8's report."""
    import io

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        for name, frame in sheets.items():
            if frame is None or frame.empty:
                continue
            # Excel sheet names cap at 31 chars and reject []:*?/\
            safe = "".join(c for c in str(name) if c not in "[]:*?/\\")[:31]
            frame.to_excel(writer, sheet_name=safe or "Sheet1")
    return buffer.getvalue()


# --- Theming --------------------------------------------------------------

_LIGHT = {
    "background": "#ffffff", "text": "#333333", "grid": "#f0f3fa",
    "border": "#d1d4dc", "plot_bg": "#ffffff",
}
_DARK = {
    "background": "#0e1117", "text": "#e6e6e6", "grid": "#26292f",
    "border": "#3a3f4b", "plot_bg": "#0e1117",
}


def theme() -> dict[str, str]:
    """Palette matching the viewer's Streamlit theme.

    The chart library paints its own canvas, so a hardcoded white background
    would sit as a bright box inside a dark page. Resolution is delegated to
    theme.palette() so the charts and the stylesheet can never disagree.
    """
    import theme as _theme

    tokens = _theme.palette()
    return {
        "background": tokens["bg"], "text": tokens["text"],
        "grid": tokens["grid"], "border": tokens["border"],
        "plot_bg": tokens["plot_bg"],
    }


def chart_base(height: int) -> dict[str, Any]:
    """Shared lightweight-charts chart options, theme-aware."""
    palette = theme()
    return {
        "height": height,
        "layout": {
            "background": {"type": "solid", "color": palette["background"]},
            "textColor": palette["text"],
        },
        "grid": {
            "vertLines": {"color": palette["grid"]},
            "horzLines": {"color": palette["grid"]},
        },
        "rightPriceScale": {"borderColor": palette["border"]},
        "timeScale": {"borderColor": palette["border"], "timeVisible": False},
        "crosshair": {"mode": 1},
    }


# --- Ticker picker --------------------------------------------------------

def ticker_picker(key: str, kinds: tuple[str, ...] = ("equities", "etfs")) -> str:
    """Watchlist dropdown plus a name search, shared by the analysis pages.

    Searching matters for non-US names: BMV listings carry an exchange suffix
    you would never guess (La Comer is LACOMERUBC.MX).
    """
    watchlist, _ = cached_get("/watchlist")
    stored, _ = cached_get("/tickers")
    known = sorted({t["ticker"] for t in (stored or {}).get("tickers", [])})
    listed = sorted({s for kind in kinds for s in (watchlist or {}).get(kind, [])})
    options = listed + [t for t in known if t not in listed]

    chosen = st.sidebar.selectbox("Ticker", options, key=f"{key}_select") if options else ""

    with st.sidebar.expander("Search for another", expanded=not options):
        query = st.text_input(
            "Company or ticker", "", key=f"{key}_q",
            placeholder="e.g. La Comer",
        ).strip()
        if query:
            payload, error = cached_get("/search", {"q": query})
            if error:
                st.caption(f"Search unavailable: {error}")
            else:
                for hit in payload["results"]:
                    label = f"**{hit['symbol']}** — {hit['name'][:34]}"
                    caption = f"{hit['exchange_label']} · {hit['type']}"
                    if st.button(label, key=f"{key}_pick_{hit['symbol']}",
                                 help=caption, width="stretch"):
                        st.session_state[f"{key}_manual"] = hit["symbol"]
                        st.rerun()
                    st.caption(caption)

    return (st.session_state.get(f"{key}_manual") or chosen or "").strip().upper()


def fetch_prompt(ticker: str, capability: str = "prices") -> bool:
    """Offer an inline fetch for a ticker that is not stored yet.

    Returns True if a fetch succeeded, so the caller can rerun.
    """
    st.warning(f"Nothing stored for **{ticker}** yet.")
    label = "Fetch prices now" if capability == "prices" else f"Fetch {capability} now"
    if st.button(label, type="primary", key=f"fetch_{capability}_{ticker}"):
        with st.spinner(f"Fetching {ticker} through the provider chain…"):
            result, error = post(f"/ingest/{capability}/{ticker}")
        if error:
            st.error(error)
            st.caption(
                "Non-US listings need their exchange suffix — BMV names look "
                "like `LACOMERUBC.MX`. Use the sidebar search to find the exact symbol."
            )
            return False
        st.success(
            f"Stored {result['rows_stored']:,} rows via **{result['provider']}**."
        )
        cached_get.clear()
        return True
    return False


def price_matrix(symbols: list[str], start: str | None = None) -> "pd.DataFrame":
    """Adjusted closes for several assets, aligned on shared trading days.

    Analytics runs on adjusted prices throughout: a split in a raw close reads
    as a ~50% one-day loss and would wreck every covariance and drawdown number.
    """
    series = {}
    for symbol in symbols:
        frame = prices_frame(symbol, start=start)
        if frame.empty:
            continue
        series[symbol] = frame["adj_close"].fillna(frame["close"]).astype(float)
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()
