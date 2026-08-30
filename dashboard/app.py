"""Tisell Terminal - dashboard entry point.

Navigation sits across the top rather than in the sidebar, so the sidebar is
free for per-view controls and the terminal reads like a workstation rather
than a docs site. Views share one open-asset workspace (see workspace.py), so
a name opened anywhere stays open everywhere.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import streamlit as st

import theme

st.set_page_config(
    page_title="Tisell Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

PAGES = {
    "Research": [
        st.Page("views/overview.py", title="Overview", icon=":material/search:", default=True),
        st.Page("views/charts.py", title="Charts", icon=":material/candlestick_chart:"),
        st.Page("views/fundamentals.py", title="Fundamentals", icon=":material/table_chart:"),
    ],
    "Markets": [
        st.Page("views/watchlist.py", title="Watchlist", icon=":material/list:"),
        st.Page("views/screener.py", title="Screener", icon=":material/filter_alt:"),
        st.Page("views/macro.py", title="Macro", icon=":material/public:"),
    ],
    "Analytics": [
        st.Page("views/pricing.py", title="Pricing", icon=":material/functions:"),
        st.Page("views/portfolio.py", title="Portfolio", icon=":material/pie_chart:"),
    ],
    "System": [
        st.Page("views/data_ops.py", title="Data", icon=":material/settings:"),
    ],
}

# The stylesheet is injected here, before the page runs, so every view
# inherits it without repeating the call.
theme.inject()

st.navigation(PAGES, position="top").run()
