"""Price charts with technical overlays, TradingView-style."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

import api
import ui
import indicators

api.require_backend()

UP, DOWN = "#26a69a", "#ef5350"
RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 365, "5Y": 1826, "ALL": None}



def line(frame: pd.Series, color: str, width: int = 2) -> dict:
    data = [
        {"time": stamp.date().isoformat(), "value": float(value)}
        for stamp, value in frame.items()
        if pd.notna(value)
    ]
    return {"type": "Line", "data": data,
            "options": {"color": color, "lineWidth": width, "priceLineVisible": False}}


# --- Controls -------------------------------------------------------------

import search_ui
import workspace

st.header("Charts")
search_ui.search_box("charts")
if not workspace.open_assets():
    # With nothing open, offer the watchlist rather than forcing a search for
    # a name the user already tracks.
    search_ui.quick_open("charts")
ticker = workspace.selector("charts")
if not ticker:
    st.info("Search for a company above, or pick one from the watchlist, to open it.")
    st.stop()

left = st.sidebar
range_label = left.radio("Range", list(RANGES), index=3, horizontal=True)

left.divider()
left.caption("Overlays")
show_ma = left.multiselect("Moving averages", [20, 50, 100, 200], default=[50, 200])
use_ema = left.toggle("Use EMA instead of SMA", value=False)
show_bb = left.toggle("Bollinger Bands (20, 2σ)", value=False)
show_volume = left.toggle("Volume", value=True)
show_rsi = left.toggle("RSI (14)", value=True)
show_macd = left.toggle("MACD (12, 26, 9)", value=False)

# --- Data -----------------------------------------------------------------

days = RANGES[range_label]
# Indicators need history before the visible window or the first N bars are
# blank, so fetch a warm-up buffer and trim after computing.
warmup = 250
fetch_start = (
    (date.today() - timedelta(days=days + warmup * 2)).isoformat() if days else None
)
frame = api.prices_frame(ticker, start=fetch_start)

if frame.empty:
    st.title(ticker)
    if api.fetch_prompt(ticker, "prices"):
        st.rerun()
    st.stop()

overlays: dict[str, pd.Series] = {}
for window in show_ma:
    label = f"{'EMA' if use_ema else 'SMA'}{window}"
    overlays[label] = (indicators.ema if use_ema else indicators.sma)(frame, window)
bands = indicators.bollinger(frame) if show_bb else None
rsi_series = indicators.rsi(frame) if show_rsi else None
macd_frame = indicators.macd(frame) if show_macd else None

visible_from = pd.Timestamp(date.today() - timedelta(days=days)) if days else frame.index[0]
view = frame[frame.index >= visible_from]
if view.empty:
    view = frame
trim = lambda s: s[s.index >= view.index[0]]

# --- Header ---------------------------------------------------------------

close = view["adj_close"].fillna(view["close"]).astype(float)
change = close.iloc[-1] - close.iloc[0]
change_pct = (change / close.iloc[0] * 100) if close.iloc[0] else 0.0
stats = indicators.performance(view)

profile, _ = api.cached_get(f"/profile/{ticker}")
_p = profile or {}
_tone = "up" if change >= 0 else "down"
ui.hero(
    ticker, _p.get("name"),
    facts=[f"{len(view):,} daily bars",
           f"{view.index[0].date()} → {view.index[-1].date()}", _p.get("sector")],
    chips=[(range_label, "info"), (f"{ui.pct(change_pct)} {range_label}", _tone)],
)

cols = st.columns(6)
cols[0].metric("Last close", f"{view['close'].iloc[-1]:,.2f}",
               f"{change:+,.2f} ({change_pct:+.2f}%)")
cols[1].metric(f"{range_label} high", f"{view['high'].max():,.2f}")
cols[2].metric(f"{range_label} low", f"{view['low'].min():,.2f}")
cols[3].metric("Ann. vol", f"{(stats['annual_vol'] or 0) * 100:,.1f}%")
cols[4].metric("Max drawdown", f"{(stats['max_drawdown'] or 0) * 100:,.1f}%")
last_volume = view["volume"].dropna()
# Raw share counts run to nine digits and overflow the card; nobody reads
# them digit by digit anyway.
cols[5].metric(
    "Last volume",
    ui.compact(float(last_volume.iloc[-1]), decimals=1) if len(last_volume) else "—",
)

# --- Charts ---------------------------------------------------------------

# Providers leave NaN on non-trading gap days. json.dumps writes those as a
# bare NaN, which is not valid JSON and kills the chart component outright, so
# incomplete bars are dropped rather than passed through.
drawable = view.dropna(subset=["open", "high", "low", "close"])
candles = [
    {"time": stamp.date().isoformat(), "open": float(row["open"]),
     "high": float(row["high"]), "low": float(row["low"]),
     "close": float(row["close"])}
    for stamp, row in drawable.iterrows()
]
skipped = len(view) - len(drawable)
series = [{
    "type": "Candlestick", "data": candles,
    "options": {"upColor": UP, "downColor": DOWN, "borderUpColor": UP,
                "borderDownColor": DOWN, "wickUpColor": UP, "wickDownColor": DOWN},
}]

palette = ["#2962ff", "#ff6d00", "#7b1fa2", "#00897b"]
for index, (label, values) in enumerate(overlays.items()):
    series.append(line(trim(values), palette[index % len(palette)]))

if bands is not None:
    series.append(line(trim(bands["upper"]), "#9598a1", 1))
    series.append(line(trim(bands["middle"]), "#9598a1", 1))
    series.append(line(trim(bands["lower"]), "#9598a1", 1))

if show_volume:
    series.append({
        "type": "Histogram",
        "data": [
            {"time": stamp.date().isoformat(),
             "value": int(row["volume"]) if pd.notna(row["volume"]) else 0,
             "color": UP if row["close"] >= row["open"] else DOWN}
            for stamp, row in drawable.iterrows()
        ],
        "options": {"priceScaleId": "volume", "priceFormat": {"type": "volume"}},
        "priceScale": {"scaleMargins": {"top": 0.8, "bottom": 0.0}},
    })

charts = [{"chart": api.chart_base(520), "series": series}]

if rsi_series is not None:
    charts.append({
        "chart": api.chart_base(160),
        "series": [line(trim(rsi_series), "#7b1fa2")],
    })

if macd_frame is not None:
    charts.append({
        "chart": api.chart_base(180),
        "series": [
            line(trim(macd_frame["macd"]), "#2962ff"),
            line(trim(macd_frame["signal"]), "#ff6d00"),
            {
                "type": "Histogram",
                "data": [
                    {"time": stamp.date().isoformat(), "value": float(value),
                     "color": UP if value >= 0 else DOWN}
                    for stamp, value in trim(macd_frame["histogram"]).items()
                    if pd.notna(value)
                ],
                "options": {"priceFormat": {"type": "price"}},
            },
        ],
    })

renderLightweightCharts(charts, key=f"{ticker}-{range_label}-{len(series)}-{len(charts)}")

if skipped:
    st.caption(
        f"{skipped} bar(s) in this window had incomplete OHLC and were not drawn."
    )

legend = "  ".join(f"**{label}**" for label in overlays)
if legend:
    st.caption(f"Overlays: {legend}" + ("  ·  **Bollinger 20/2σ**" if show_bb else ""))
if rsi_series is not None:
    st.caption(f"RSI(14) now: **{rsi_series.iloc[-1]:.1f}** — 70+ overbought, 30− oversold.")

with st.expander("Raw bars / export"):
    st.dataframe(view.sort_index(ascending=False), width="stretch")
    st.download_button(
        "Download bars (.xlsx)",
        api.to_excel({ticker: view.sort_index(ascending=False)}),
        file_name=f"{ticker}_prices_{date.today().isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
