"""Portfolio construction, risk and reporting (v1 items 7 and 8)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import api
from analytics import risk as rk

api.require_backend()

st.header("Portfolio & risk")

# --- Universe -------------------------------------------------------------

watchlist, _ = api.cached_get("/watchlist")
stored, _ = api.cached_get("/tickers")
available = sorted({t["ticker"] for t in (stored or {}).get("tickers", [])})

if len(available) < 2:
    st.warning("Need at least two tickers with stored prices. Fetch some on the **Data** page.")
    st.stop()

default = [s for s in ((watchlist or {}).get("equities", []) +
                       (watchlist or {}).get("etfs", []) +
                       (watchlist or {}).get("mexico", [])) if s in available][:8]

with st.sidebar:
    st.subheader("Portfolio")
    selected = st.multiselect("Assets", available, default=default or available[:6])
    years = st.slider("History (years)", 1, 10, 3)
    risk_free = st.number_input("Risk-free rate (annual)", value=0.045, min_value=0.0,
                                max_value=0.20, step=0.005, format="%.4f")
    # Beta is meaningless against an arbitrary single stock, so the broad-market
    # proxies sort first rather than whatever happens to be alphabetically first.
    PREFERRED_BENCHMARKS = ("SPY", "IVV", "VOO", "QQQ", "NAFTRACISHRS.MX", "^MXX")
    ordered = ([b for b in PREFERRED_BENCHMARKS if b in available]
               + [s for s in available if s not in PREFERRED_BENCHMARKS])
    benchmark = st.selectbox(
        "Benchmark for beta", ordered, index=0,
        help="S&P 500 (SPY) for USD/US-listed assets; a BMV proxy for MXN names. "
             "Beta against an unrelated single stock is not meaningful.",
    )

if len(selected) < 2:
    st.info("Pick at least two assets in the sidebar.")
    st.stop()

start = (date.today() - timedelta(days=365 * years + 40)).isoformat()
prices = api.price_matrix(sorted(set(selected + [benchmark])), start=start)

if prices.empty:
    st.warning("No overlapping price history for that selection.")
    st.stop()

returns_all = rk.daily_returns(prices)
missing = [s for s in selected if s not in returns_all.columns]
if missing:
    st.warning(f"Dropped for insufficient overlapping history: {', '.join(missing)}")

assets = [s for s in selected if s in returns_all.columns]
if len(assets) < 2:
    st.error("Fewer than two assets survived the overlap filter.")
    st.stop()

returns = returns_all[assets]
benchmark_returns = returns_all[benchmark] if benchmark in returns_all.columns else None

st.caption(
    f"{len(returns):,} overlapping trading days · "
    f"{returns.index[0].date()} → {returns.index[-1].date()} · "
    f"adjusted closes"
)

# --- Weights --------------------------------------------------------------

st.subheader("Allocation")
method = st.radio(
    "Method",
    ["Equal weight", "Max Sharpe", "Minimum variance", "Risk parity", "Custom"],
    horizontal=True,
)
cap = st.slider("Maximum weight per asset", 0.05, 1.0, 0.40, 0.05,
                disabled=method in ("Equal weight", "Risk parity", "Custom"))

optimised: dict | None = None
try:
    if method == "Equal weight":
        weights = pd.Series(1 / len(assets), index=assets)
    elif method == "Max Sharpe":
        optimised = rk.mean_variance(returns, risk_free, "max_sharpe", max_weight=cap)
        weights = optimised["weights"]
    elif method == "Minimum variance":
        optimised = rk.mean_variance(returns, risk_free, "min_variance", max_weight=cap)
        weights = optimised["weights"]
    elif method == "Risk parity":
        optimised = rk.risk_parity(returns, risk_free)
        weights = optimised["weights"]
    else:
        raw = {a: st.sidebar.number_input(f"{a} %", 0.0, 100.0,
                                          100.0 / len(assets), 1.0, key=f"w_{a}")
               for a in assets}
        total = sum(raw.values())
        if total <= 0:
            st.error("Custom weights must sum to more than zero.")
            st.stop()
        weights = pd.Series({a: v / total for a, v in raw.items()})
except (ValueError, Exception) as exc:  # optimiser can fail on degenerate input
    st.error(f"Optimisation failed: {exc}")
    st.stop()

allocation, chart_col = st.columns([1, 1])
with allocation:
    shown = weights[weights.abs() > 0.0005].sort_values(ascending=False)
    st.dataframe(
        shown.to_frame("Weight").style.format("{:.2%}"),
        width="stretch", height=min(360, 60 + 36 * len(shown)),
    )
with chart_col:
    palette = api.theme()
    pie = px.pie(values=shown.values, names=shown.index, hole=0.55)
    pie.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0),
                      paper_bgcolor=palette["plot_bg"], font_color=palette["text"],
                      showlegend=True)
    st.plotly_chart(pie, width="stretch")

portfolio = rk.portfolio_returns(returns, weights)

# --- Metrics --------------------------------------------------------------

st.subheader("Risk-adjusted performance")
portfolio_metrics = rk.metrics(portfolio, risk_free, benchmark_returns)

row = st.columns(6)
row[0].metric("CAGR", f"{(portfolio_metrics['cagr'] or 0):.2%}")
row[1].metric("Ann. vol", f"{(portfolio_metrics['annual_vol'] or 0):.2%}")
row[2].metric("Sharpe", f"{portfolio_metrics['sharpe']:.2f}" if portfolio_metrics["sharpe"] else "—")
row[3].metric("Sortino", f"{portfolio_metrics['sortino']:.2f}" if portfolio_metrics["sortino"] else "—")
row[4].metric("Max drawdown", f"{(portfolio_metrics['max_drawdown'] or 0):.2%}")
row[5].metric(f"Beta vs {benchmark}",
              f"{portfolio_metrics['beta']:.2f}" if portfolio_metrics["beta"] else "—")

per_asset = pd.DataFrame({
    asset: rk.metrics(returns[asset], risk_free, benchmark_returns)
    for asset in assets
}).T
per_asset["weight"] = weights.reindex(per_asset.index)
display_columns = ["weight", "cagr", "annual_vol", "sharpe", "sortino",
                   "max_drawdown", "beta", "alpha", "var_95", "cvar_95"]
st.dataframe(
    per_asset[display_columns].style.format({
        "weight": "{:.2%}", "cagr": "{:.2%}", "annual_vol": "{:.2%}",
        "sharpe": "{:.2f}", "sortino": "{:.2f}", "max_drawdown": "{:.2%}",
        "beta": "{:.2f}", "alpha": "{:.2%}", "var_95": "{:.2%}", "cvar_95": "{:.2%}",
    }, na_rep="—"),
    width="stretch",
)

# --- Growth curve ---------------------------------------------------------

curve = (1 + portfolio).cumprod()
bench_curve = (1 + benchmark_returns.reindex(portfolio.index).fillna(0)).cumprod() \
    if benchmark_returns is not None else None

figure = go.Figure()
figure.add_trace(go.Scatter(x=curve.index, y=curve.values, name="Portfolio",
                            line=dict(width=2)))
if bench_curve is not None:
    figure.add_trace(go.Scatter(x=bench_curve.index, y=bench_curve.values,
                                name=benchmark, line=dict(width=1.5, dash="dot")))
palette = api.theme()
figure.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10),
                     hovermode="x unified", legend=dict(orientation="h", y=1.1),
                     plot_bgcolor=palette["plot_bg"], paper_bgcolor=palette["plot_bg"],
                     font_color=palette["text"], title="Growth of 1 unit")
figure.update_xaxes(showgrid=True, gridcolor=palette["grid"])
figure.update_yaxes(showgrid=True, gridcolor=palette["grid"])
st.plotly_chart(figure, width="stretch")

# --- Correlation ----------------------------------------------------------

st.subheader("Correlation & covariance")
correlation = rk.correlation(returns)
heatmap = px.imshow(correlation, text_auto=".2f", aspect="auto",
                    color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
heatmap.update_layout(height=90 + 42 * len(assets), margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor=palette["plot_bg"], font_color=palette["text"])
st.plotly_chart(heatmap, width="stretch")

covariance = rk.covariance(returns)
with st.expander("Annualised covariance matrix"):
    st.dataframe(covariance.style.format("{:.5f}"), width="stretch")

# --- Tail risk ------------------------------------------------------------

st.subheader("Value at risk")
var_left, var_right = st.columns(2)
confidence = var_left.select_slider("Confidence", [0.90, 0.95, 0.99], value=0.95)
horizon = var_right.select_slider("Horizon (trading days)", [1, 5, 10, 21], value=1)

historical = rk.historical_var(portfolio, confidence)
simulated = rk.monte_carlo_var(portfolio, confidence, horizon)

var_cols = st.columns(4)
var_cols[0].metric(f"Historical VaR ({confidence:.0%}, 1d)", f"{historical['var']:.2%}")
var_cols[1].metric("Historical CVaR", f"{historical['cvar']:.2%}")
var_cols[2].metric(f"MC VaR ({horizon}d)", f"{simulated['var']:.2%}")
var_cols[3].metric("MC CVaR", f"{simulated['cvar']:.2%}")
st.caption(
    f"Monte Carlo bootstraps {simulated['simulations']:,} draws from actual "
    "historical days rather than assuming normality, which keeps the fat tails "
    "that matter for a tail-risk number. CVaR is the average loss beyond VaR."
)

# --- Stress tests ---------------------------------------------------------

st.subheader("Scenario stress tests")

# Market-wide scenarios are expressed against the benchmark, which normally is
# NOT one of the holdings. Shock it inside a frame that includes it, with a
# zero weight, so the move propagates to every holding by its own beta instead
# of the scenario silently dropping for want of a matching column.
stress_returns = returns_all[[*assets, benchmark]] if benchmark in returns_all.columns else returns
stress_weights = weights.reindex(stress_returns.columns).fillna(0.0)

scenarios: dict[str, dict[str, float]] = {}
if benchmark in stress_returns.columns:
    scenarios[f"Market −10% ({benchmark})"] = {benchmark: -0.10}
    scenarios[f"Market −20% ({benchmark})"] = {benchmark: -0.20}
    scenarios[f"Market +10% ({benchmark})"] = {benchmark: 0.10}
for name, shocks in rk.DEFAULT_SCENARIOS.items():
    narrowed = {a: v for a, v in shocks.items() if a in stress_returns.columns}
    if narrowed and not set(narrowed) <= {benchmark}:
        scenarios[name] = narrowed

stress = rk.stress_test(stress_weights, stress_returns, scenarios) if scenarios else pd.DataFrame()
if not stress.empty:
    # The benchmark's own move is the scenario input, not a portfolio holding.
    stress = stress.drop(columns=[f"{benchmark} move"], errors="ignore")

if stress.empty:
    st.caption("No scenario applies to this selection.")
else:
    st.dataframe(
        stress.style.format("{:.2%}").background_gradient(
            cmap="RdYlGn", subset=["portfolio_return"], vmin=-0.25, vmax=0.25),
        width="stretch",
    )
    st.caption(
        "Named assets take the stated shock; everything else moves by its "
        "historical beta to them, so a partial scenario still gives a "
        "whole-portfolio answer."
    )

# --- Efficient frontier ---------------------------------------------------

with st.expander("Efficient frontier"):
    with st.spinner("Solving the frontier…"):
        frontier = rk.efficient_frontier(returns, points=25, max_weight=cap)
    if frontier.empty:
        st.caption("The optimiser could not trace a frontier for this selection.")
    else:
        scatter = go.Figure()
        scatter.add_trace(go.Scatter(
            x=frontier["volatility"], y=frontier["expected_return"],
            mode="lines+markers", name="Frontier"))
        scatter.add_trace(go.Scatter(
            x=[portfolio_metrics["annual_vol"]], y=[portfolio_metrics["annual_return"]],
            mode="markers", name="This portfolio",
            marker=dict(size=14, symbol="star")))
        scatter.add_trace(go.Scatter(
            x=[rk.metrics(returns[a])["annual_vol"] for a in assets],
            y=[rk.metrics(returns[a])["annual_return"] for a in assets],
            mode="markers+text", text=assets, textposition="top center",
            name="Individual assets", marker=dict(size=8)))
        scatter.update_layout(
            height=420, margin=dict(l=10, r=10, t=30, b=10),
            xaxis_title="Annualised volatility", yaxis_title="Expected annual return",
            plot_bgcolor=palette["plot_bg"], paper_bgcolor=palette["plot_bg"],
            font_color=palette["text"], legend=dict(orientation="h", y=1.12))
        scatter.update_xaxes(showgrid=True, gridcolor=palette["grid"], tickformat=".0%")
        scatter.update_yaxes(showgrid=True, gridcolor=palette["grid"], tickformat=".0%")
        st.plotly_chart(scatter, width="stretch")

# --- Report export (item 8) ----------------------------------------------

st.divider()
st.subheader("Report")
st.caption(
    "Bundles holdings, per-asset and portfolio risk metrics, the correlation "
    "and covariance matrices, VaR and the stress tests into one workbook."
)

summary = pd.DataFrame([{
    "Generated": date.today().isoformat(),
    "Method": method,
    "Assets": len(assets),
    "History start": returns.index[0].date().isoformat(),
    "History end": returns.index[-1].date().isoformat(),
    "Trading days": len(returns),
    "Risk-free": risk_free,
    "Benchmark": benchmark,
    **{k: v for k, v in portfolio_metrics.items() if v is not None},
}]).T.rename(columns={0: "Value"})

sheets = {
    "Summary": summary,
    "Holdings": weights.to_frame("Weight"),
    "Asset metrics": per_asset[display_columns],
    "Correlation": correlation,
    "Covariance": covariance,
    "VaR": pd.DataFrame([historical, simulated]),
    "Stress tests": stress,
    "Returns": returns,
}
if optimised is not None:
    sheets["Risk contribution"] = optimised["risk_contribution"].to_frame("Share")

st.download_button(
    "Download portfolio report (.xlsx)",
    api.to_excel(sheets),
    file_name=f"portfolio_report_{date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
