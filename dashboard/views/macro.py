"""Macro and rates, from FRED."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import api

api.require_backend()

st.header("Macro")

payload, error = api.cached_get("/macro")
series = (payload or {}).get("series", [])
if error or not series:
    st.warning(error or "No macro series stored.")
    st.code("python data-layer/refresh.py --what macro", language="bash")
    st.stop()

labels = {s["series_id"]: f"{s['series_id']} — {(s['title'] or '')[:60]}" for s in series}
chosen = st.sidebar.multiselect(
    "Series", list(labels), default=list(labels)[:3], format_func=lambda s: labels[s]
)
years = st.sidebar.slider("Years of history", 1, 40, 10)
normalise = st.sidebar.toggle(
    "Rebase to 100", value=False,
    help="Compare series with different units on one axis.",
)

start = (date.today() - timedelta(days=365 * years)).isoformat()

if not chosen:
    st.info("Pick one or more series in the sidebar.")
    st.stop()

figure = go.Figure()
frames: dict[str, pd.DataFrame] = {}

for series_id in chosen:
    data, err = api.cached_get(f"/macro/{series_id}", {"start": start})
    if err or not data:
        st.warning(f"{series_id}: {err}")
        continue
    frame = pd.DataFrame(data["observations"]).dropna(subset=["value"])
    if frame.empty:
        continue
    frame["date"] = pd.to_datetime(frame["date"])
    frames[series_id] = frame.set_index("date")

    values = frame["value"]
    if normalise:
        values = values / values.iloc[0] * 100

    figure.add_trace(go.Scatter(
        x=frame["date"], y=values, name=series_id, mode="lines",
        hovertemplate=f"<b>{series_id}</b><br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
    ))

palette = api.theme()
figure.update_layout(
    height=520, margin=dict(l=10, r=10, t=30, b=10),
    hovermode="x unified", legend=dict(orientation="h", y=1.08),
    yaxis_title="Rebased to 100" if normalise else None,
    plot_bgcolor=palette["plot_bg"], paper_bgcolor=palette["plot_bg"],
    font_color=palette["text"],
)
figure.update_xaxes(showgrid=True, gridcolor=palette["grid"])
figure.update_yaxes(showgrid=True, gridcolor=palette["grid"])
st.plotly_chart(figure, width="stretch")

st.subheader("Latest")
latest_rows = []
for series_id, frame in frames.items():
    meta = next((s for s in series if s["series_id"] == series_id), {})
    values = frame["value"]
    latest_rows.append({
        "Series": series_id,
        "Title": (meta.get("title") or "")[:60],
        "Units": meta.get("units"),
        "Latest": values.iloc[-1],
        "Date": frame.index[-1].date().isoformat(),
        "Δ 1y": values.iloc[-1] - values[values.index <= frame.index[-1] - pd.Timedelta(days=365)].iloc[-1]
        if (values.index <= frame.index[-1] - pd.Timedelta(days=365)).any() else None,
    })

if latest_rows:
    table = pd.DataFrame(latest_rows).set_index("Series")
    st.dataframe(
        table.style.format({"Latest": "{:,.2f}", "Δ 1y": "{:+,.2f}"}, na_rep="—"),
        width="stretch",
    )
    st.download_button(
        "Download macro (.xlsx)",
        api.to_excel({"Latest": table, **{k: v for k, v in frames.items()}}),
        file_name=f"macro_{date.today().isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
