"""Option pricing and DCF valuation (v1 items 6)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import api
import indicators
import search_ui
import workspace
from analytics import dcf as dcf_engine
from analytics import pricing as bs

api.require_backend()

st.header("Pricing & valuation")
options_tab, dcf_tab = st.tabs(["Options", "DCF"])

# =========================================================================
# Options
# =========================================================================

with options_tab:
    st.caption(
        "Black-Scholes closed form, with a Monte Carlo cross-check. Spot and "
        "realised volatility can be pulled from any stored ticker."
    )

    search_ui.search_box("pricing")
    symbol = workspace.selector("pricing")

    spot_default, vol_default = 100.0, 0.20
    if symbol:
        frame = api.prices_frame(symbol, start=(date.today() - timedelta(days=400)).isoformat())
        if not frame.empty:
            close = frame["adj_close"].fillna(frame["close"]).astype(float)
            spot_default = float(close.iloc[-1])
            realised = indicators.performance(frame).get("annual_vol")
            if realised:
                vol_default = float(realised)
            st.caption(
                f"**{symbol}** — spot {spot_default:,.2f}, "
                f"1y realised vol {vol_default:.1%} (used as the defaults below)"
            )

    left, middle, right = st.columns(3)
    S = left.number_input("Spot (S)", value=float(round(spot_default, 2)), min_value=0.01, step=1.0)
    K = left.number_input("Strike (K)", value=float(round(spot_default, 2)), min_value=0.01, step=1.0)
    days = middle.number_input("Days to expiry", value=90, min_value=0, step=1)
    sigma = middle.number_input("Volatility σ (annual)", value=float(round(vol_default, 4)),
                                min_value=0.0, max_value=5.0, step=0.01, format="%.4f")
    r = right.number_input("Risk-free r", value=0.045, min_value=-0.05, max_value=0.30,
                           step=0.005, format="%.4f")
    q = right.number_input("Dividend yield q", value=0.0, min_value=0.0, max_value=0.30,
                           step=0.005, format="%.4f")

    T = days / 365.0

    call = bs.black_scholes(S, K, T, r, sigma, q, "call")
    put = bs.black_scholes(S, K, T, r, sigma, q, "put")

    st.subheader("Price and Greeks")
    table = pd.DataFrame({
        "Call": call.as_dict(),
        "Put": put.as_dict(),
    }).T
    # Vega per 1 vol point and theta per day are the units traders actually use.
    table["vega (per 1% vol)"] = table["vega"] / 100
    table["theta (per day)"] = table["theta"] / 365
    st.dataframe(
        table[["price", "delta", "gamma", "vega (per 1% vol)", "theta (per day)", "rho"]]
        .style.format("{:,.4f}"),
        width="stretch",
    )

    parity_lhs = call.price - put.price
    parity_rhs = S * np.exp(-q * T) - K * np.exp(-r * T)
    st.caption(
        f"Put–call parity check: C − P = {parity_lhs:,.6f} vs "
        f"Se⁻qT − Ke⁻rT = {parity_rhs:,.6f} (difference {abs(parity_lhs - parity_rhs):.2e})"
    )

    st.divider()
    st.subheader("Monte Carlo cross-check")
    mc_left, mc_right = st.columns([1, 2])
    option_type = mc_left.radio("Option", ["call", "put"], horizontal=True)
    paths = mc_left.select_slider("Paths", [10_000, 50_000, 100_000, 250_000, 500_000],
                                  value=100_000)

    if T > 0 and sigma > 0:
        mc = bs.monte_carlo(S, K, T, r, sigma, q, option_type, paths=paths)
        inside = mc["ci_low"] <= mc["closed_form"] <= mc["ci_high"]
        with mc_right:
            cols = st.columns(3)
            cols[0].metric("Monte Carlo", f"{mc['price']:,.4f}",
                           f"± {mc['std_error']:.4f} s.e.")
            cols[1].metric("Closed form", f"{mc['closed_form']:,.4f}")
            cols[2].metric("Difference", f"{mc['difference']:+,.4f}")
            st.caption(
                f"95% CI {mc['ci_low']:,.4f} – {mc['ci_high']:,.4f} over "
                f"{mc['paths']:,} antithetic paths. Closed form is "
                + ("**inside** the interval, so the simulation has converged."
                   if inside else
                   "**outside** the interval — raise the path count.")
            )

        simulated = bs.simulate_paths(S, T, r, sigma, q, paths=120,
                                      steps=max(int(days), 2))
        figure = go.Figure()
        palette = api.theme()
        for row in simulated[:60]:
            figure.add_trace(go.Scatter(y=row, mode="lines", line=dict(width=0.7),
                                        opacity=0.35, showlegend=False,
                                        hoverinfo="skip"))
        figure.add_hline(y=K, line_dash="dash", annotation_text="strike")
        figure.update_layout(
            height=320, margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor=palette["plot_bg"], paper_bgcolor=palette["plot_bg"],
            font_color=palette["text"], title="Simulated GBM paths (60 shown)",
        )
        figure.update_xaxes(showgrid=True, gridcolor=palette["grid"], title="trading step")
        figure.update_yaxes(showgrid=True, gridcolor=palette["grid"])
        st.plotly_chart(figure, width="stretch")
    else:
        st.info("Set days to expiry and volatility above zero to run a simulation.")

    st.divider()
    st.subheader("Implied volatility")
    iv_left, iv_right = st.columns([1, 2])
    market_price = iv_left.number_input("Observed option price", value=float(round(call.price, 4)),
                                        min_value=0.0, step=0.10, format="%.4f")
    iv_type = iv_left.radio("Type", ["call", "put"], horizontal=True, key="iv_type")
    solved = bs.implied_vol(market_price, S, K, T, r, q, iv_type)
    if solved is None:
        iv_right.warning(
            "No volatility reproduces that price — it sits outside the "
            "no-arbitrage band for these inputs."
        )
    else:
        iv_right.metric("Implied volatility", f"{solved:.2%}",
                        f"{(solved - sigma) * 100:+.2f} pts vs input σ")

# =========================================================================
# DCF
# =========================================================================

with dcf_tab:
    st.caption(
        "Two-stage DCF: explicit free-cash-flow forecast, then a Gordon-growth "
        "terminal value, discounted at WACC. Defaults are pulled from stored "
        "fundamentals where available."
    )

    dcf_symbol = workspace.selector("dcf") or symbol
    fcf_default, debt_default, shares_default = 1_000_000_000.0, 0.0, 0.0

    if dcf_symbol:
        fundamentals = api.fundamentals_frame(dcf_symbol, "annual")
        if not fundamentals.empty:
            newest = fundamentals["period_end"].max()
            latest = fundamentals[fundamentals["period_end"] == newest]

            def latest_value(names: list[str]) -> float | None:
                hit = latest[latest["metric"].isin(names)]
                if hit.empty or pd.isna(hit["value"].iloc[0]):
                    return None
                return float(hit["value"].iloc[0])

            fcf = latest_value(["freeCashFlow", "cash_flow_free_cash_flow"])
            if fcf is None:
                operating = latest_value(["operatingCashFlow", "netCashProvidedByOperatingActivities"])
                capex = latest_value(["capitalExpenditure"])
                if operating is not None:
                    # capex is reported negative; adding it nets the outflow.
                    fcf = operating + (capex or 0.0)
            if fcf:
                fcf_default = fcf
            total_debt = latest_value(["totalDebt"]) or 0.0
            cash = latest_value(["cashAndCashEquivalents", "cashAndShortTermInvestments"]) or 0.0
            debt_default = total_debt - cash
            shares_default = latest_value(
                ["weightedAverageShsOutDil", "weightedAverageShsOut",
                 "income_statement_diluted_weighted_average_shares_outstanding"]
            ) or 0.0
            st.caption(f"Defaults from **{dcf_symbol}**, period ending {newest}.")
        else:
            st.caption(f"No stored fundamentals for {dcf_symbol} — enter values by hand.")

    c1, c2, c3 = st.columns(3)
    fcf_input = c1.number_input("Base free cash flow", value=float(fcf_default),
                                step=1e8, format="%.0f")
    growth = c1.slider("FCF growth (explicit years)", -0.20, 0.40, 0.08, 0.005)
    years = c2.slider("Forecast years", 3, 15, 5)
    terminal_growth = c2.slider("Terminal growth", -0.01, 0.06, 0.025, 0.0025)
    discount = c3.slider("Discount rate (WACC)", 0.03, 0.25, 0.09, 0.0025)
    net_debt = c3.number_input("Net debt", value=float(debt_default), step=1e8, format="%.0f")
    shares = st.number_input("Shares outstanding (0 to skip per-share)",
                             value=float(shares_default), step=1e6, format="%.0f")

    try:
        result = dcf_engine.run_dcf(dcf_engine.DCFInputs(
            free_cash_flow=fcf_input, growth_rate=growth,
            terminal_growth=terminal_growth, discount_rate=discount,
            years=years, net_debt=net_debt, shares_outstanding=shares,
        ))
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    out = st.columns(4)
    out[0].metric("Enterprise value", f"{result.enterprise_value / 1e9:,.2f}B")
    out[1].metric("Equity value", f"{result.equity_value / 1e9:,.2f}B")
    out[2].metric("Value per share",
                  f"{result.value_per_share:,.2f}" if result.value_per_share else "—")
    out[3].metric("Terminal share of EV", f"{result.terminal_share:.0%}")

    for warning in result.warnings:
        st.warning(warning)

    if result.value_per_share and dcf_symbol:
        frame = api.prices_frame(dcf_symbol, start=(date.today() - timedelta(days=20)).isoformat())
        if not frame.empty:
            market = float(frame["close"].iloc[-1])
            upside = result.value_per_share / market - 1
            st.metric(f"Implied upside vs market ({market:,.2f})", f"{upside:+.1%}")

    st.subheader("Projected cash flows")
    projections = pd.DataFrame(result.projections).set_index("year")
    st.dataframe(
        projections.style.format({
            "free_cash_flow": "{:,.0f}", "discount_factor": "{:.4f}",
            "present_value": "{:,.0f}",
        }),
        width="stretch",
    )

    st.subheader("Sensitivity — value per share")
    rates = [round(discount + step, 4) for step in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    growths = [round(terminal_growth + step, 4) for step in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    grid = pd.DataFrame(dcf_engine.sensitivity(
        dcf_engine.DCFInputs(fcf_input, growth, terminal_growth, discount, years,
                             net_debt, shares),
        rates, growths,
    )).set_index("discount_rate")
    grid.index = [f"{value:.2%}" for value in grid.index]
    grid.index.name = "WACC \\ terminal g"
    st.dataframe(
        grid.style.format("{:,.2f}", na_rep="n/a")
            .background_gradient(cmap="RdYlGn", axis=None),
        width="stretch",
    )
    st.caption(
        "Rows are WACC, columns terminal growth. Blank cells are combinations "
        "where growth meets or exceeds the discount rate and the model is undefined."
    )
