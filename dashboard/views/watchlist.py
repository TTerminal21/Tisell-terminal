"""Tisell Terminal - watchlist overview.

Reads only from the FastAPI data layer, never from DuckDB or a provider
directly, so the TUI can sit beside it on the same endpoints.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import api
import ui
import indicators
import workspace

OVERVIEW_PAGE = "views/overview.py"


health = api.require_backend()

st.header("Watchlist")
ui.section("Markets")
st.caption(
    f"{health['tickers']} tickers · {health['prices']:,} bars · "
    f"{health['fundamentals']:,} fundamentals · {health['macro_observations']:,} macro points"
)

# --- Macro strip ----------------------------------------------------------

MACRO_STRIP = [
    ("DGS10", "US 10Y", "%"), ("DGS2", "US 2Y", "%"),
    ("T10Y2Y", "10Y–2Y", "%"), ("FEDFUNDS", "Fed funds", "%"),
    ("UNRATE", "Unemployment", "%"), ("DEXMXUS", "MXN/USD", ""),
]

series_payload, _ = api.cached_get("/macro")
available = {s["series_id"] for s in (series_payload or {}).get("series", [])}

strip = [item for item in MACRO_STRIP if item[0] in available]
if strip:
    ui.section("Macro")
    columns = st.columns(len(strip))
    for column, (series_id, label, unit) in zip(columns, strip):
        payload, error = api.cached_get(
            f"/macro/{series_id}",
            {"start": (date.today() - timedelta(days=400)).isoformat()},
        )
        if error or not payload:
            column.metric(label, "—")
            continue
        points = [o for o in payload["observations"] if o["value"] is not None]
        if not points:
            column.metric(label, "—")
            continue
        latest = points[-1]
        # Compare against the last print on or before one year ago, so the
        # label matches the maths on daily and monthly series alike.
        cutoff = (date.fromisoformat(latest["date"]) - timedelta(days=365)).isoformat()
        earlier = [o for o in points if o["date"] <= cutoff]
        prior = earlier[-1]["value"] if earlier else None
        delta = latest["value"] - prior if prior is not None else None
        column.metric(
            label,
            f"{latest['value']:,.2f}{unit}",
            f"{delta:+.2f} vs 1y" if delta is not None else None,
            delta_color="normal",
        )
        column.caption(latest["date"])

st.divider()

# --- Watchlist ------------------------------------------------------------

watchlist, wl_error = api.cached_get("/watchlist")
tickers_payload, _ = api.cached_get("/tickers")
stored = {t["ticker"]: t for t in (tickers_payload or {}).get("tickers", [])}

if wl_error or not watchlist:
    st.warning(wl_error or "No watchlist configured.")
    st.stop()

GROUP_LABELS = {"equities": "US equities", "mexico": "Mexico (BMV)", "etfs": "ETFs"}
available_groups = [g for g in GROUP_LABELS if watchlist.get(g)]
if not available_groups:
    st.info("Watchlist is empty. Add names on the **Data** page.")
    st.stop()

# The watchlist is now a few hundred names; loading every one on every render
# would mean a few hundred API round-trips, so it is filtered by group and
# capped rather than loaded wholesale.
chosen_groups = st.multiselect(
    "Groups", available_groups, default=available_groups[:1],
    format_func=lambda g: f"{GROUP_LABELS[g]} ({len(watchlist.get(g, []))})",
)
symbols = [s for g in chosen_groups for s in watchlist.get(g, [])]
if not symbols:
    st.info("Pick at least one group above.")
    st.stop()

MAX_ROWS = 60
if len(symbols) > MAX_ROWS:
    st.caption(f"Showing the first {MAX_ROWS} of {len(symbols)} — narrow the groups to see others.")
    symbols = symbols[:MAX_ROWS]

WINDOWS = {"1D": 1, "1W": 7, "1M": 31, "3M": 92, "6M": 183, "1Y": 365}
lookback = (date.today() - timedelta(days=400)).isoformat()

rows = []
missing = []
progress = st.progress(0.0, text="Loading watchlist…")
for index, symbol in enumerate(symbols, start=1):
    progress.progress(index / len(symbols), text=f"Loading {symbol}…")
    frame = api.prices_frame(symbol, start=lookback)
    if frame.empty:
        missing.append(symbol)
        continue

    close = frame["adj_close"].fillna(frame["close"]).astype(float)
    last = close.iloc[-1]

    record = {
        "Ticker": symbol,
        "Name": (stored.get(symbol) or {}).get("name") or "",
        "Sector": (stored.get(symbol) or {}).get("sector") or "",
        "Last": last,
    }
    for label, days in WINDOWS.items():
        # Anchored on the latest bar, not today: EOD data is routinely a day
        # or more stale, and anchoring on today makes 1D compare the last bar
        # with itself and report +0.00%.
        cutoff = close.index[-1] - pd.Timedelta(days=days)
        window = close[close.index <= cutoff]
        base = window.iloc[-1] if not window.empty else None
        record[label] = ((last / base - 1) * 100) if base else None

    stats = indicators.performance(frame)
    record["Vol (1y)"] = (stats["annual_vol"] or 0) * 100
    record["Max DD"] = (stats["max_drawdown"] or 0) * 100
    record["RSI"] = indicators.rsi(frame).iloc[-1]
    record["As of"] = frame.index[-1].date().isoformat()
    rows.append(record)
progress.empty()

if missing:
    st.warning(
        f"No stored bars for: {', '.join(missing)}. "
        "Run a refresh on the **Data** page."
    )

if not rows:
    st.stop()

table = pd.DataFrame(rows).set_index("Ticker")

percent_columns = [*WINDOWS, "Vol (1y)", "Max DD"]
styler = table.style.format(
    {"Last": "{:,.2f}", "RSI": "{:.0f}", **{c: "{:+.2f}%" for c in percent_columns}},
    na_rep="—",
).background_gradient(cmap="RdYlGn", subset=list(WINDOWS), vmin=-15, vmax=15)

st.dataframe(styler, width="stretch", height=min(680, 60 + 36 * len(table)))

with st.expander("Export"):
    st.download_button(
        "Download watchlist (.xlsx)",
        api.to_excel({"Watchlist": table}),
        file_name=f"watchlist_{date.today().isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.caption("Returns and volatility use the split/dividend-adjusted close.")

st.divider()
st.caption("Open in the workspace")
for row_start in range(0, len(table.index), 8):
    for column, symbol in zip(st.columns(8), list(table.index)[row_start:row_start + 8]):
        if column.button(symbol, key=f"wl_open_{symbol}", width="stretch"):
            workspace.open_asset(symbol)
            st.switch_page(OVERVIEW_PAGE)
