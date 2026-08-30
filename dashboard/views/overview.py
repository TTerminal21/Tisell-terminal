"""Asset overview - search anything, see its general info at a glance."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

import api
import ui
import indicators
import search_ui
import workspace

api.require_backend()

st.header("Overview")
search_ui.search_box("overview")
search_ui.quick_open("overview")
st.divider()

symbol = workspace.selector("overview")
if not symbol:
    st.info("Search for a company above, or pick one from the watchlist, to open it.")
    st.stop()

profile, profile_error = api.cached_get(f"/profile/{symbol}")
frame = api.prices_frame(symbol, start=(date.today() - timedelta(days=740)).isoformat())

if frame.empty:
    st.subheader(symbol)
    if api.fetch_prompt(symbol, "prices"):
        st.rerun()
    st.stop()

# Profile is cheap and often missing on a freshly-opened name; offer it inline.
if profile_error and st.button(f"Fetch company info for {symbol}", key="ov_prof"):
    with st.spinner("Fetching profile…"):
        _, error = api.post(f"/ingest/profile/{symbol}")
    if error:
        st.error(error)
    else:
        api.cached_get.clear()
        st.rerun()

name = (profile or {}).get("name") or symbol
_p = profile or {}
ui.hero(
    symbol, name if name != symbol else None,
    facts=[_p.get("sector"), _p.get("industry"), _p.get("country")],
    chips=[(_p.get("exchange") or "", "neutral"), (_p.get("currency") or "", "neutral")],
)

# --- Key stats ------------------------------------------------------------

close = frame["adj_close"].fillna(frame["close"]).astype(float)
last = close.iloc[-1]
year = close[close.index >= pd.Timestamp(date.today() - timedelta(days=365))]
stats = indicators.performance(frame[frame.index >= year.index[0]]) if len(year) > 1 else {}


def change_over(days: int) -> float | None:
    """Percent change vs the last bar at least `days` before the latest one.

    Anchored on the latest *bar*, not on today: EOD data is routinely a day or
    more stale, and anchoring on today makes the 1-day change compare the last
    bar with itself and print +0.00%.
    """
    cutoff = frame.index[-1] - pd.Timedelta(days=days)
    window = close[close.index <= cutoff]
    return ((last / window.iloc[-1] - 1) * 100) if not window.empty else None


row = st.columns(6)
day_change = change_over(1)
row[0].metric("Last", f"{last:,.2f}",
              f"{day_change:+.2f}%" if day_change is not None else None)
row[1].metric("1M", f"{change_over(31):+.2f}%" if change_over(31) is not None else "—")
row[2].metric("1Y", f"{change_over(365):+.2f}%" if change_over(365) is not None else "—")
row[3].metric("52w range",
              f"{year.min():,.2f}–{year.max():,.2f}" if len(year) else "—")
row[4].metric("Ann. vol", f"{(stats.get('annual_vol') or 0) * 100:,.1f}%")
row[5].metric("Max DD", f"{(stats.get('max_drawdown') or 0) * 100:,.1f}%")

if profile:
    second = st.columns(4)
    market_cap = profile.get("market_cap")
    second[0].metric("Market cap",
                     f"{market_cap / 1e9:,.1f}B" if market_cap else "—")
    second[1].metric("Beta", f"{profile['beta']:.2f}" if profile.get("beta") else "—")
    second[2].metric("As of", frame.index[-1].date().isoformat())
    second[3].metric("Bars (2y window)", f"{len(frame):,}")

# --- Price ----------------------------------------------------------------

drawable = frame.dropna(subset=["open", "high", "low", "close"])
view = drawable[drawable.index >= pd.Timestamp(date.today() - timedelta(days=365))]
if view.empty:
    view = drawable

renderLightweightCharts([{
    "chart": api.chart_base(320),
    "series": [{
        "type": "Area",
        "data": [
            {"time": stamp.date().isoformat(), "value": float(value)}
            for stamp, value in view["adj_close"].fillna(view["close"]).items()
            if pd.notna(value)
        ],
        "options": {"lineColor": "#2962ff", "topColor": "rgba(41,98,255,0.28)",
                    "bottomColor": "rgba(41,98,255,0.02)", "lineWidth": 2},
    }],
}], key=f"ov_{symbol}")

# --- Fundamentals snapshot ------------------------------------------------

ui.section("Snapshot")
fundamentals = api.fundamentals_frame(symbol, "annual")

if fundamentals.empty:
    st.caption(
        "No fundamentals stored. ETFs and many non-US listings have none to "
        "fetch — only prices and a profile."
    )
    if st.button(f"Try fetching fundamentals for {symbol}", key="ov_fund"):
        with st.spinner("Fetching…"):
            _, error = api.post(f"/ingest/fundamentals/{symbol}")
        if error:
            st.error(error)
        else:
            api.cached_get.clear()
            st.rerun()
else:
    HEADLINE = [
        ("Revenue", ["revenue", "income_statement_total_revenues"], "money"),
        ("Net income", ["netIncome", "income_statement_net_income"], "money"),
        ("P/E", ["priceToEarningsRatio"], "x"),
        ("Net margin", ["netProfitMargin"], "pct"),
        ("ROE", ["returnOnEquity"], "pct"),
        ("Debt/Equity", ["debtToEquityRatio"], "x"),
    ]
    newest = fundamentals["period_end"].max()
    latest = fundamentals[fundamentals["period_end"] == newest]

    cards = []
    for label, names, kind in HEADLINE:
        hit = latest[latest["metric"].isin(names)]
        if hit.empty or pd.isna(hit["value"].iloc[0]):
            continue
        value = float(hit["value"].iloc[0])
        if kind == "money":
            shown = f"{value / 1e9:,.2f}B"
        elif kind == "pct":
            shown = f"{value * 100:,.2f}%"
        else:
            shown = f"{value:,.2f}x"
        cards.append((label, shown))

    if cards:
        st.caption(f"Latest annual period: {newest}")
        for column, (label, shown) in zip(st.columns(len(cards)), cards):
            column.metric(label, shown)
    else:
        st.caption(f"Stored fundamentals carry no headline metrics for {newest}.")

if (profile or {}).get("description"):
    with st.expander("Business description"):
        st.write(profile["description"])
