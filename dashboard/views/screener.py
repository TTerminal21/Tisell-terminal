"""Screener across the watchlist, on stored data only."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import api
import indicators

api.require_backend()

st.header("Screener")
st.caption(
    "Runs over what is already in DuckDB — no provider calls, so it costs no quota. "
    "Refresh on the **Data** page to widen coverage."
)

# Ratio names as FMP stores them; the screener pins to whichever source has them.
RATIOS = {
    "P/E": "priceToEarningsRatio",
    "P/B": "priceToBookRatio",
    "P/S": "priceToSalesRatio",
    "Gross margin": "grossProfitMargin",
    "Net margin": "netProfitMargin",
    "ROE": "returnOnEquity",
    "Debt/Equity": "debtToEquityRatio",
    "Current ratio": "currentRatio",
}
PERCENT_RATIOS = {"Gross margin", "Net margin", "ROE"}

watchlist, _ = api.cached_get("/watchlist")
GROUP_LABELS = {"equities": "US equities", "mexico": "Mexico (BMV)", "etfs": "ETFs"}
available_groups = [g for g in GROUP_LABELS if (watchlist or {}).get(g)]
chosen_groups = st.sidebar.multiselect(
    "Groups", available_groups, default=available_groups[:1],
    format_func=lambda g: f"{GROUP_LABELS[g]} ({len((watchlist or {}).get(g, []))})",
)
symbols = sorted({s for g in chosen_groups for s in (watchlist or {}).get(g, [])})
if not symbols:
    st.info("Pick at least one group in the sidebar.")
    st.stop()
MAX_SCREEN = 80
if len(symbols) > MAX_SCREEN:
    st.caption(f"Screening the first {MAX_SCREEN} of {len(symbols)} names.")
    symbols = symbols[:MAX_SCREEN]

lookback = (date.today() - timedelta(days=400)).isoformat()
tickers_payload, _ = api.cached_get("/tickers")
meta = {t["ticker"]: t for t in (tickers_payload or {}).get("tickers", [])}

rows = []
progress = st.progress(0.0, text="Screening…")
for index, symbol in enumerate(symbols, start=1):
    progress.progress(index / len(symbols), text=f"Screening {symbol}…")
    frame = api.prices_frame(symbol, start=lookback)
    if frame.empty:
        continue

    close = frame["adj_close"].fillna(frame["close"]).astype(float)
    stats = indicators.performance(frame)
    record: dict[str, object] = {
        "Ticker": symbol,
        "Sector": (meta.get(symbol) or {}).get("sector") or "",
        "Last": close.iloc[-1],
        "1M %": None, "6M %": None, "1Y %": None,
        "Vol %": (stats["annual_vol"] or 0) * 100,
        "Max DD %": (stats["max_drawdown"] or 0) * 100,
        "RSI": indicators.rsi(frame).iloc[-1],
    }
    for label, days in (("1M %", 31), ("6M %", 183), ("1Y %", 365)):
        # Anchored on the latest bar rather than today, so stale EOD data does
        # not silently shorten every window.
        window = close[close.index <= close.index[-1] - pd.Timedelta(days=days)]
        if not window.empty:
            record[label] = (close.iloc[-1] / window.iloc[-1] - 1) * 100

    fundamentals = api.fundamentals_frame(symbol, "annual")
    if not fundamentals.empty:
        ratio_rows = fundamentals[fundamentals["metric"].isin(RATIOS.values())]
        if not ratio_rows.empty:
            # Pin to the single source with the widest ratio coverage. Mixing
            # providers here would put one company's P/E and another's ROE in
            # the same row, from different periods.
            best_source = ratio_rows["source"].value_counts().index[0]
            ratio_rows = ratio_rows[ratio_rows["source"] == best_source]
            newest = ratio_rows["period_end"].max()
            newest_rows = ratio_rows[ratio_rows["period_end"] == newest]
            record["Ratios from"] = f"{best_source} {newest}"
            for label, metric in RATIOS.items():
                hit = newest_rows[newest_rows["metric"] == metric]
                if not hit.empty and pd.notna(hit["value"].iloc[0]):
                    value = float(hit["value"].iloc[0])
                    record[label] = value * 100 if label in PERCENT_RATIOS else value
    rows.append(record)
progress.empty()

if not rows:
    st.warning("No stored data for the watchlist yet.")
    st.stop()

table = pd.DataFrame(rows).set_index("Ticker")

# Fundamentals arrive by rotation over several days, so a freshly-expanded
# watchlist shows mostly-empty ratio columns. Say so, rather than leaving a
# wall of "None" that reads like a fault.
with_ratios = int(table["Ratios from"].notna().sum()) if "Ratios from" in table else 0
if with_ratios < len(table):
    st.info(
        f"{with_ratios} of {len(table)} names have fundamentals stored. Ratio "
        "columns fill in as the refresh rotation works through the watchlist — "
        "a full pass costs more than one day of FMP's free tier. Price and risk "
        "columns are complete."
    )

# --- Filters --------------------------------------------------------------

st.sidebar.header("Filters")
sectors = sorted({s for s in table["Sector"] if s})
if sectors:
    picked = st.sidebar.multiselect("Sector", sectors, default=sectors)
    table = table[table["Sector"].isin(picked) | (table["Sector"] == "")]

numeric = [c for c in table.columns if c != "Sector" and pd.api.types.is_numeric_dtype(table[c])]
for column in st.sidebar.multiselect("Filter on", numeric, default=[]):
    values = table[column].dropna()
    if values.empty:
        continue
    low, high = float(values.min()), float(values.max())
    if low == high:
        continue
    lo, hi = st.sidebar.slider(column, low, high, (low, high))
    table = table[table[column].between(lo, hi) | table[column].isna()]

sort_by = st.sidebar.selectbox("Sort by", numeric, index=numeric.index("1Y %") if "1Y %" in numeric else 0)
ascending = st.sidebar.toggle("Ascending", value=False)
table = table.sort_values(sort_by, ascending=ascending, na_position="last")

percent_columns = [c for c in table.columns if "%" in c]
formats = {c: "{:,.2f}" for c in numeric}
formats.update({c: "{:+.2f}%" for c in percent_columns})
formats["Last"] = "{:,.2f}"
formats["RSI"] = "{:.0f}"

st.dataframe(
    table.style.format(formats, na_rep="—").background_gradient(
        cmap="RdYlGn", subset=[c for c in ("1M %", "6M %", "1Y %") if c in table],
        vmin=-25, vmax=25,
    ),
    width="stretch", height=min(700, 60 + 36 * len(table)),
)
st.caption(f"{len(table)} of {len(rows)} names after filters.")

st.download_button(
    "Download screen (.xlsx)",
    api.to_excel({"Screen": table}),
    file_name=f"screen_{date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
