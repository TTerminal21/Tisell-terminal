"""Fundamentals: valuation, profitability, liquidity and leverage, plus statements."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import api
import labels
import ui

api.require_backend()

# Metric names differ per provider. FMP's names are the primary set; the
# fallbacks let a Fiscal.ai-sourced ticker still populate the same cards.
RATIO_GROUPS: dict[str, list[tuple[str, list[str], str]]] = {
    "Valuation": [
        ("P/E", ["priceToEarningsRatio", "peRatio"], "x"),
        ("P/B", ["priceToBookRatio", "pbRatio"], "x"),
        ("P/S", ["priceToSalesRatio"], "x"),
        ("EV/EBITDA", ["evToEBITDA", "enterpriseValueOverEBITDA"], "x"),
        ("Dividend yield", ["dividendYield"], "%"),
    ],
    "Profitability": [
        ("Gross margin", ["grossProfitMargin"], "%"),
        ("Operating margin", ["operatingProfitMargin"], "%"),
        ("Net margin", ["netProfitMargin"], "%"),
        ("ROE", ["returnOnEquity"], "%"),
        ("ROA", ["returnOnAssets"], "%"),
        ("ROIC", ["returnOnInvestedCapital", "returnOnCapitalEmployed"], "%"),
    ],
    "Liquidity": [
        ("Current ratio", ["currentRatio"], "x"),
        ("Quick ratio", ["quickRatio"], "x"),
        ("Cash ratio", ["cashRatio"], "x"),
    ],
    "Leverage": [
        ("Debt/Equity", ["debtToEquityRatio", "debtEquityRatio"], "x"),
        ("Debt/Assets", ["debtToAssetsRatio"], "x"),
        ("Interest coverage", ["interestCoverageRatio", "interestCoverage"], "x"),
    ],
}

STATEMENTS = {
    "income": "Income statement",
    "balance": "Balance sheet",
    "cash_flow": "Cash flow",
    "ratios": "Ratios",
    "metrics": "Key metrics",
    "segments": "Segments & KPIs",
    "comprehensive_income": "Comprehensive income",
    "equity_changes": "Changes in equity",
    "notes": "Notes & annexes",
    "as_filed": "As filed (SEC)",
    "as_filed_cnbv": "As filed (CNBV)",
}


def pick(frame: pd.DataFrame, names: list[str], period: str) -> float | None:
    for name in names:
        hit = frame[(frame["metric"] == name) & (frame["period_end"] == period)]
        if not hit.empty and pd.notna(hit["value"].iloc[0]):
            return float(hit["value"].iloc[0])
    return None


# --- Controls -------------------------------------------------------------

import search_ui
import workspace

st.header("Fundamentals")
search_ui.search_box("fundamentals")
if not workspace.open_assets():
    # With nothing open, offer the watchlist rather than forcing a search for
    # a name the user already tracks.
    search_ui.quick_open("fundamentals")
ticker = workspace.selector("fundamentals")
if not ticker:
    st.info("Search for a company above, or pick one from the watchlist, to open it.")
    st.stop()
period_type = st.sidebar.radio("Period", ["annual", "quarterly"], horizontal=True)

frame = api.fundamentals_frame(ticker, period_type)

# CNBV files quarterly and only Q4 carries full-year figures, so a Mexican
# name often has quarterly data and no annual. Falling back beats telling the
# user "nothing stored" when the other period is sitting right there.
if frame.empty:
    other = "quarterly" if period_type == "annual" else "annual"
    fallback = api.fundamentals_frame(ticker, other)
    if not fallback.empty:
        st.info(
            f"No **{period_type}** statements stored for {ticker}, showing "
            f"**{other}** instead. Mexican issuers file quarterly with the CNBV."
        )
        frame, period_type = fallback, other

profile, _ = api.cached_get(f"/profile/{ticker}")
_p = profile or {}
ui.hero(
    ticker, _p.get("name"),
    facts=[_p.get("sector"), _p.get("industry"), _p.get("exchange")],
    chips=[(_p.get("currency") or "", "neutral"), (period_type, "info")],
)

if frame.empty:
    if api.fetch_prompt(ticker, "fundamentals"):
        st.rerun()
    st.caption(
        "ETFs and many non-US listings have no statements to fetch — only "
        "prices and a profile."
    )
    st.stop()

# Default to whichever stored source actually carries ratio metrics - otherwise
# the ratio cards land empty on a name whose fundamentals came from a fallback.
ratio_names = {name for group in RATIO_GROUPS.values() for _, names, _ in group for name in names}
by_ratio_coverage = (
    frame[frame["metric"].isin(ratio_names)]["source"].value_counts().index.tolist()
)
sources = by_ratio_coverage + [s for s in sorted(frame["source"].unique())
                               if s not in by_ratio_coverage]
source = st.sidebar.selectbox(
    "Source", sources,
    help="Providers store the same figure under different names, so views are "
         "pinned to one source rather than mixing them. Sources carrying ratio "
         "metrics are listed first.",
)
scoped = frame[frame["source"] == source]
periods = sorted(scoped["period_end"].unique(), reverse=True)

if not periods:
    st.warning(f"No {period_type} data from {source}.")
    st.stop()

period = st.sidebar.selectbox("As of", periods)

# --- Headline -------------------------------------------------------------

if profile and profile.get("market_cap"):
    top = st.columns(4)
    top[0].metric("Market cap", ui.compact(profile["market_cap"], decimals=1))
    if profile.get("beta"):
        top[1].metric("Beta", f"{profile['beta']:.2f}")
    top[2].metric("Periods stored", len(periods))
    top[3].metric("Source", source)

st.divider()

# --- Ratio cards ----------------------------------------------------------

any_ratio = False
for group, definitions in RATIO_GROUPS.items():
    values = [(label, pick(scoped, names, period), unit) for label, names, unit in definitions]
    values = [v for v in values if v[1] is not None or v[0] == "Interest coverage"]
    values = [v for v in values if not (v[1] is None and v[0] != "Interest coverage")]
    if not values:
        continue
    any_ratio = True
    ui.section(group)
    columns = st.columns(len(values))
    for column, (label, value, unit) in zip(columns, values):
        if label == "Interest coverage" and not value:
            # Issuers that net interest into "other income" report no interest
            # expense, so the ratio degenerates to 0. Showing 0.00x would read
            # as "cannot cover its interest", which is the opposite of true.
            column.metric(label, "n/a")
            column.caption("no interest expense reported")
        elif unit == "%":
            # Ratio endpoints return margins as fractions, not percentages.
            column.metric(label, f"{value * 100:,.2f}%")
        else:
            column.metric(label, f"{value:,.2f}{unit}")

if not any_ratio:
    st.info(
        f"No ratio metrics from **{source}** for this period. "
        "FMP carries the richest ratio set — try switching source, or refresh "
        "fundamentals so FMP serves as primary."
    )

st.divider()

# --- Statements -----------------------------------------------------------

ui.section("Statements")
present = [s for s in STATEMENTS if s in set(scoped["statement"].unique())]
tabs = st.tabs([STATEMENTS[s] for s in present])

exports: dict[str, pd.DataFrame] = {}
for tab, statement in zip(tabs, present):
    pivot = api.statement_pivot(scoped, statement, source=source)
    if not pivot.empty:
        # Raw taxonomy ids are precise and unreadable; show the words an
        # analyst uses. Filed order is preserved, only the labels change.
        pivot = pivot.copy()
        pivot.index = pd.Index(labels.humanize_index(pivot.index), name="Line item")
        pivot.columns = [str(c) for c in pivot.columns]
    exports[STATEMENTS[statement]] = pivot

    with tab:
        if pivot.empty:
            st.caption("Nothing stored.")
            continue
        search = st.text_input(
            "Filter", "", key=f"filter_{statement}",
            placeholder="e.g. revenue, cash, debt", label_visibility="collapsed",
        ).strip()
        shown = pivot[pivot.index.str.contains(search, case=False)] if search else pivot
        if shown.empty:
            st.caption(f"No line item matches {search!r}.")
            continue
        st.dataframe(
            shown.style.format("{:,.0f}", na_rep="—"),
            width="stretch", height=min(620, 60 + 32 * len(shown)),
        )
        st.caption(
            f"{len(shown):,} line items · {len(pivot.columns)} periods · "
            f"as filed, in statement order · source: {source}"
        )

st.divider()
st.download_button(
    f"Download {ticker} {period_type} statements (.xlsx)",
    api.to_excel(exports),
    file_name=f"{ticker}_{period_type}_{date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
