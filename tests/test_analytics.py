"""Tests for the analytics layer.

These exist because every bug found in this project so far was caught by
reading output by hand, and two of them (risk parity converging to the wrong
fixed point, FMP silently demoted by a limit cap) were completely silent. An
optimiser that returns confident nonsense does not announce itself.

Run:  .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics import dcf, pricing, risk  # noqa: E402


# --- Black-Scholes --------------------------------------------------------

# Reference values for S=100, K=100, T=1, r=5%, sigma=20%, q=0.
REF = {"call": 10.450583572185565, "put": 5.573526022256971}


def test_black_scholes_matches_reference():
    call = pricing.black_scholes(100, 100, 1, 0.05, 0.20, option="call")
    put = pricing.black_scholes(100, 100, 1, 0.05, 0.20, option="put")
    assert call.price == pytest.approx(REF["call"], abs=1e-9)
    assert put.price == pytest.approx(REF["put"], abs=1e-9)
    assert call.delta == pytest.approx(0.6368306511756191, abs=1e-9)
    assert call.gamma == pytest.approx(0.018762017345846895, abs=1e-9)
    assert call.vega == pytest.approx(37.52403469169379, abs=1e-6)


def test_put_call_parity():
    S, K, T, r, q = 123.0, 110.0, 0.75, 0.043, 0.012
    call = pricing.black_scholes(S, K, T, r, 0.27, q, "call")
    put = pricing.black_scholes(S, K, T, r, 0.27, q, "put")
    expected = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert (call.price - put.price) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("option", ["call", "put"])
def test_implied_vol_round_trip(option):
    price = pricing.black_scholes(100, 95, 0.5, 0.04, 0.33, 0.01, option).price
    assert pricing.implied_vol(price, 100, 95, 0.5, 0.04, 0.01, option) == pytest.approx(0.33, abs=1e-6)


def test_implied_vol_below_intrinsic_is_none():
    # Deep ITM call priced under its own floor: no positive vol reaches it.
    assert pricing.implied_vol(0.01, 150, 100, 1, 0.05, option="call") is None


def test_zero_time_is_intrinsic():
    assert pricing.black_scholes(120, 100, 0, 0.05, 0.2, option="call").price == pytest.approx(20.0)
    assert pricing.black_scholes(120, 100, 0, 0.05, 0.2, option="put").price == pytest.approx(0.0)


def test_monte_carlo_brackets_closed_form():
    result = pricing.monte_carlo(100, 100, 1, 0.05, 0.20, option="call",
                                 paths=400_000, seed=7)
    assert result["ci_low"] <= result["closed_form"] <= result["ci_high"]


def test_invalid_option_type_rejected():
    with pytest.raises(ValueError):
        pricing.black_scholes(100, 100, 1, 0.05, 0.2, option="straddle")


# --- DCF ------------------------------------------------------------------

def test_dcf_matches_hand_calculation():
    result = dcf.run_dcf(dcf.DCFInputs(
        free_cash_flow=100, growth_rate=0.0, terminal_growth=0.0,
        discount_rate=0.10, years=5,
    ))
    explicit = sum(100 / 1.1 ** n for n in range(1, 6))
    terminal_pv = (100 / 0.10) / 1.1 ** 5
    assert result.enterprise_value == pytest.approx(explicit + terminal_pv, abs=1e-9)


def test_dcf_rejects_growth_above_discount():
    with pytest.raises(ValueError):
        dcf.run_dcf(dcf.DCFInputs(100, 0.0, 0.12, 0.10))


def test_dcf_warns_when_terminal_value_dominates():
    result = dcf.run_dcf(dcf.DCFInputs(100, 0.02, 0.035, 0.04, years=3))
    assert result.terminal_share > 0.80
    assert any("terminal value" in w for w in result.warnings)


def test_wacc_and_capm():
    assert dcf.wacc(60, 40, 0.10, 0.05, 0.25) == pytest.approx(0.075)
    assert dcf.capm_cost_of_equity(0.04, 1.2, 0.05) == pytest.approx(0.10)


# --- Risk -----------------------------------------------------------------

@pytest.fixture
def returns() -> pd.DataFrame:
    """Correlated synthetic returns with deliberately different vols."""
    rng = np.random.default_rng(11)
    n = 900
    market = rng.normal(0.0004, 0.010, n)
    data = {
        "LOWVOL": 0.4 * market + rng.normal(0.0002, 0.004, n),
        "MIDVOL": 0.9 * market + rng.normal(0.0003, 0.008, n),
        "HIGHVOL": 1.6 * market + rng.normal(0.0005, 0.018, n),
        "HEDGE": -0.3 * market + rng.normal(0.0002, 0.006, n),
    }
    index = pd.bdate_range("2021-01-01", periods=n)
    return pd.DataFrame(data, index=index)


def test_beta_against_self_is_one(returns):
    assert risk.metrics(returns["MIDVOL"], benchmark=returns["MIDVOL"])["beta"] == pytest.approx(1.0)


def test_hedge_has_negative_beta(returns):
    assert risk.metrics(returns["HEDGE"], benchmark=returns["MIDVOL"])["beta"] < 0


def test_cvar_is_at_least_as_severe_as_var(returns):
    result = risk.historical_var(returns["HIGHVOL"], 0.95)
    assert result["cvar"] <= result["var"]


def test_monte_carlo_var_cvar_ordering(returns):
    result = risk.monte_carlo_var(returns["HIGHVOL"], 0.95, horizon_days=5, seed=3)
    assert result["cvar"] <= result["var"] < 0


def test_risk_parity_equalises_risk_contributions(returns):
    """The bug this suite exists for: contributions ranged 0.05%-45%."""
    result = risk.risk_parity(returns)
    contributions = result["risk_contribution"]
    assert contributions.max() - contributions.min() < 1e-3
    assert contributions.sum() == pytest.approx(1.0)


def test_min_variance_is_not_riskier_than_max_sharpe(returns):
    sharpe = risk.mean_variance(returns, 0.02, "max_sharpe", max_weight=0.6)
    minvar = risk.mean_variance(returns, 0.02, "min_variance", max_weight=0.6)
    assert minvar["volatility"] <= sharpe["volatility"] + 1e-9


def test_optimiser_respects_long_only_and_cap(returns):
    result = risk.mean_variance(returns, 0.02, "max_sharpe", max_weight=0.35)
    weights = result["weights"]
    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert weights.min() >= -1e-9
    assert weights.max() <= 0.35 + 1e-6


def test_optimiser_allows_shorts_when_asked(returns):
    result = risk.mean_variance(returns, 0.02, "min_variance",
                                max_weight=1.0, allow_short=True)
    assert result["weights"].sum() == pytest.approx(1.0, abs=1e-6)


def test_needs_two_assets(returns):
    with pytest.raises(ValueError):
        risk.mean_variance(returns[["LOWVOL"]], 0.0, "min_variance")


def test_stress_test_agrees_with_beta(returns):
    """A market shock must reproduce weight-averaged beta times the shock."""
    weights = pd.Series(0.25, index=returns.columns)
    shocked = risk.stress_test(weights, returns, {"down": {"MIDVOL": -0.20}})
    portfolio = risk.portfolio_returns(returns, weights)
    beta = risk.metrics(portfolio, benchmark=returns["MIDVOL"])["beta"]
    assert shocked.loc["down", "portfolio_return"] == pytest.approx(beta * -0.20, abs=5e-3)


def test_daily_returns_drops_assets_without_overlap():
    index = pd.bdate_range("2022-01-03", periods=100)
    frame = pd.DataFrame({
        "GOOD": np.linspace(100, 130, 100),
        "SPARSE": [np.nan] * 90 + list(np.linspace(10, 12, 10)),
    }, index=index)
    assert "SPARSE" not in risk.daily_returns(frame).columns
    assert "GOOD" in risk.daily_returns(frame).columns


def test_portfolio_returns_weight_alignment(returns):
    weights = pd.Series({"LOWVOL": 0.5, "MIDVOL": 0.5})  # HIGHVOL/HEDGE omitted
    combined = risk.portfolio_returns(returns, weights)
    expected = returns[["LOWVOL", "MIDVOL"]].mean(axis=1)
    pd.testing.assert_series_equal(combined, expected, check_names=False)
