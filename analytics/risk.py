"""Portfolio construction and risk.

Everything takes a wide DataFrame of *adjusted* closes (index = date, one
column per asset) and works off simple daily returns. Using adjusted prices is
not optional: a split shows up in raw closes as a ~50% single-day loss, which
would wreck every covariance and drawdown number here.

Trading-day count is 252 throughout.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# --- Return series --------------------------------------------------------

def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Simple daily returns, with assets that never trade together dropped."""
    frame = prices.sort_index().astype(float)
    returns = frame.pct_change().replace([np.inf, -np.inf], np.nan)
    # An asset needs overlapping history with the rest or it poisons the
    # covariance matrix with NaNs; require at least 30 shared observations.
    return returns.dropna(axis=1, thresh=30).dropna()


def covariance(returns: pd.DataFrame, annualise: bool = True) -> pd.DataFrame:
    cov = returns.cov()
    return cov * TRADING_DAYS if annualise else cov


def correlation(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.corr()


# --- Single-asset and portfolio metrics ------------------------------------

def metrics(returns: pd.Series, risk_free: float = 0.0,
            benchmark: pd.Series | None = None) -> dict[str, float | None]:
    """Risk-adjusted return stats for one return series.

    `risk_free` is an annual rate; it is de-annualised before use so the Sharpe
    numerator is a genuine excess return rather than a mismatch of horizons.
    """
    series = returns.dropna()
    if series.empty:
        return {k: None for k in
                ("total_return", "cagr", "annual_return", "annual_vol", "sharpe",
                 "sortino", "max_drawdown", "calmar", "var_95", "cvar_95", "beta", "alpha")}

    daily_rf = (1 + risk_free) ** (1 / TRADING_DAYS) - 1
    excess = series - daily_rf

    annual_return = float(series.mean() * TRADING_DAYS)
    annual_vol = float(series.std(ddof=1) * np.sqrt(TRADING_DAYS))

    curve = (1 + series).cumprod()
    total_return = float(curve.iloc[-1] - 1)
    years = len(series) / TRADING_DAYS
    cagr = float(curve.iloc[-1] ** (1 / years) - 1) if years > 0 else None

    drawdown = curve / curve.cummax() - 1
    max_drawdown = float(drawdown.min())

    # Sortino penalises only downside deviation, which is the honest measure
    # for asymmetric return distributions.
    downside = excess[excess < 0]
    downside_vol = float(downside.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0

    sharpe = float(excess.mean() * TRADING_DAYS / annual_vol) if annual_vol else None
    sortino = float(excess.mean() * TRADING_DAYS / downside_vol) if downside_vol else None
    calmar = float(cagr / abs(max_drawdown)) if cagr is not None and max_drawdown else None

    result: dict[str, float | None] = {
        "total_return": total_return,
        "cagr": cagr,
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "var_95": float(np.percentile(series, 5)),
        "cvar_95": float(series[series <= np.percentile(series, 5)].mean()),
        "beta": None,
        "alpha": None,
    }

    if benchmark is not None:
        aligned = pd.concat([series, benchmark.dropna()], axis=1, join="inner").dropna()
        if len(aligned) > 30:
            asset, bench = aligned.iloc[:, 0], aligned.iloc[:, 1]
            variance = float(bench.var(ddof=1))
            if variance > 0:
                beta = float(asset.cov(bench) / variance)
                result["beta"] = beta
                # Jensen's alpha, annualised.
                result["alpha"] = float(
                    (asset.mean() - daily_rf - beta * (bench.mean() - daily_rf))
                    * TRADING_DAYS
                )
    return result


def portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    aligned = weights.reindex(returns.columns).fillna(0.0)
    return returns.mul(aligned, axis=1).sum(axis=1)


# --- Optimisation ---------------------------------------------------------

def _clean_weights(raw: np.ndarray, columns: pd.Index,
                   floor: float = 1e-4) -> pd.Series:
    """Zero out dust and renormalise, so weights read as an actual allocation."""
    weights = pd.Series(np.asarray(raw).flatten(), index=columns)
    weights[weights.abs() < floor] = 0.0
    total = weights.sum()
    return weights / total if total else weights


def mean_variance(returns: pd.DataFrame, risk_free: float = 0.0,
                  objective: str = "max_sharpe",
                  target_return: float | None = None,
                  max_weight: float = 1.0,
                  allow_short: bool = False) -> dict[str, Any]:
    """Long-only (by default) mean-variance optimisation via cvxpy."""
    import cvxpy as cp

    if returns.shape[1] < 2:
        raise ValueError("need at least two assets to optimise")

    mu = returns.mean().values * TRADING_DAYS
    sigma = returns.cov().values * TRADING_DAYS
    n = len(mu)

    # Nudge the covariance matrix to be positive semi-definite. Sample
    # covariance from short or highly-correlated history often is not, and
    # cvxpy will refuse the problem rather than return a wrong answer.
    sigma = (sigma + sigma.T) / 2
    eigenvalues = np.linalg.eigvalsh(sigma)
    if eigenvalues.min() < 0:
        sigma = sigma + np.eye(n) * (abs(eigenvalues.min()) + 1e-10)

    w = cp.Variable(n)
    constraints = [cp.sum(w) == 1, w <= max_weight]
    constraints.append(w >= (-max_weight if allow_short else 0))

    variance = cp.quad_form(w, cp.psd_wrap(sigma))

    if objective == "min_variance":
        problem = cp.Problem(cp.Minimize(variance), constraints)
    elif objective == "target_return":
        if target_return is None:
            raise ValueError("target_return is required for objective='target_return'")
        constraints.append(mu @ w >= target_return)
        problem = cp.Problem(cp.Minimize(variance), constraints)
    elif objective == "max_sharpe":
        # Maximising the Sharpe ratio directly is non-convex, so this sweeps
        # the efficient frontier and keeps the best point - reliable, and it
        # gives us the frontier for free.
        return _max_sharpe_by_frontier(returns, mu, sigma, risk_free,
                                       max_weight, allow_short)
    else:
        raise ValueError(f"unknown objective {objective!r}")

    problem.solve()
    if w.value is None:
        raise ValueError(f"optimiser failed ({problem.status})")

    weights = _clean_weights(w.value, returns.columns)
    return _describe(weights, mu, sigma, risk_free, returns.columns)


def _max_sharpe_by_frontier(returns: pd.DataFrame, mu: np.ndarray,
                            sigma: np.ndarray, risk_free: float,
                            max_weight: float, allow_short: bool,
                            points: int = 40) -> dict[str, Any]:
    import cvxpy as cp

    n = len(mu)
    best: dict[str, Any] | None = None
    lower, upper = float(mu.min()), float(mu.max())

    for target in np.linspace(lower, upper, points):
        w = cp.Variable(n)
        constraints = [cp.sum(w) == 1, w <= max_weight, mu @ w >= target]
        constraints.append(w >= (-max_weight if allow_short else 0))
        problem = cp.Problem(cp.Minimize(cp.quad_form(w, cp.psd_wrap(sigma))), constraints)
        try:
            problem.solve()
        except cp.error.SolverError:
            continue
        if w.value is None:
            continue
        weights = _clean_weights(w.value, returns.columns)
        described = _describe(weights, mu, sigma, risk_free, returns.columns)
        if best is None or (described["sharpe"] or -np.inf) > (best["sharpe"] or -np.inf):
            best = described
    if best is None:
        raise ValueError("optimiser could not find a feasible portfolio")
    return best


def risk_parity(returns: pd.DataFrame, risk_free: float = 0.0,
                iterations: int = 20_000) -> dict[str, Any]:
    """Equal risk contribution, solved by the standard fixed-point iteration."""
    sigma = (returns.cov().values * TRADING_DAYS)
    mu = returns.mean().values * TRADING_DAYS
    n = sigma.shape[0]

    weights = np.ones(n) / n
    for _ in range(iterations):
        marginal = sigma @ weights
        # Risk contribution of asset i is w_i * (Sigma w)_i. We want each to
        # equal the average, so scale by target/contribution - dividing by the
        # marginal instead converges to the wrong fixed point entirely.
        contribution = weights * marginal
        target = contribution.sum() / n
        adjustment = np.divide(target, contribution, out=np.ones_like(contribution),
                               where=contribution > 0)
        updated = weights * adjustment ** 0.5  # damped, or it oscillates
        updated = np.clip(updated, 1e-10, None)
        updated /= updated.sum()
        if np.abs(updated - weights).max() < 1e-12:
            weights = updated
            break
        weights = updated

    return _describe(_clean_weights(weights, returns.columns), mu, sigma,
                     risk_free, returns.columns)


def _describe(weights: pd.Series, mu: np.ndarray, sigma: np.ndarray,
              risk_free: float, columns: pd.Index) -> dict[str, Any]:
    vector = weights.reindex(columns).fillna(0.0).values
    expected = float(mu @ vector)
    volatility = float(np.sqrt(vector @ sigma @ vector))
    marginal = sigma @ vector
    contributions = vector * marginal
    total = contributions.sum()
    return {
        "weights": weights,
        "expected_return": expected,
        "volatility": volatility,
        "sharpe": (expected - risk_free) / volatility if volatility else None,
        "risk_contribution": pd.Series(
            contributions / total if total else contributions, index=columns
        ),
    }


def efficient_frontier(returns: pd.DataFrame, points: int = 30,
                       max_weight: float = 1.0) -> pd.DataFrame:
    """Risk/return pairs along the long-only frontier, for plotting."""
    import cvxpy as cp

    mu = returns.mean().values * TRADING_DAYS
    sigma = returns.cov().values * TRADING_DAYS
    sigma = (sigma + sigma.T) / 2
    n = len(mu)

    rows = []
    for target in np.linspace(float(mu.min()), float(mu.max()), points):
        w = cp.Variable(n)
        problem = cp.Problem(
            cp.Minimize(cp.quad_form(w, cp.psd_wrap(sigma))),
            [cp.sum(w) == 1, w >= 0, w <= max_weight, mu @ w >= target],
        )
        try:
            problem.solve()
        except cp.error.SolverError:
            continue
        if w.value is None:
            continue
        vector = np.asarray(w.value).flatten()
        rows.append({
            "expected_return": float(mu @ vector),
            "volatility": float(np.sqrt(vector @ sigma @ vector)),
        })
    return pd.DataFrame(rows)


# --- Tail risk ------------------------------------------------------------

def historical_var(returns: pd.Series, confidence: float = 0.95) -> dict[str, float]:
    """VaR/CVaR straight from the empirical distribution - no normality assumed."""
    series = returns.dropna()
    percentile = (1 - confidence) * 100
    var = float(np.percentile(series, percentile))
    tail = series[series <= var]
    return {
        "var": var,
        "cvar": float(tail.mean()) if len(tail) else var,
        "confidence": confidence,
        "observations": int(len(series)),
    }


def monte_carlo_var(returns: pd.Series, confidence: float = 0.95,
                    horizon_days: int = 1, simulations: int = 50_000,
                    seed: int | None = 42,
                    bootstrap: bool = True) -> dict[str, float]:
    """Simulated VaR/CVaR over a horizon.

    Bootstrapping resamples actual historical days, which keeps the fat tails
    that a normal parametrisation throws away — and the tail is the entire
    point of a VaR number.
    """
    series = returns.dropna().values
    if len(series) < 30:
        raise ValueError("need at least 30 return observations")

    rng = np.random.default_rng(seed)
    if bootstrap:
        draws = rng.choice(series, size=(simulations, horizon_days), replace=True)
    else:
        draws = rng.normal(series.mean(), series.std(ddof=1),
                           size=(simulations, horizon_days))

    horizon_returns = (1 + draws).prod(axis=1) - 1
    percentile = (1 - confidence) * 100
    var = float(np.percentile(horizon_returns, percentile))
    tail = horizon_returns[horizon_returns <= var]

    return {
        "var": var,
        "cvar": float(tail.mean()) if len(tail) else var,
        "confidence": confidence,
        "horizon_days": horizon_days,
        "simulations": simulations,
        "method": "bootstrap" if bootstrap else "normal",
    }


def stress_test(weights: pd.Series, returns: pd.DataFrame,
                scenarios: dict[str, dict[str, float]]) -> pd.DataFrame:
    """Apply named shocks to assets and report the portfolio hit.

    A shock given for an asset is applied directly; assets not named in a
    scenario are moved by their historical beta to the shocked names, so a
    partial scenario still produces a whole-portfolio answer.
    """
    aligned = weights.reindex(returns.columns).fillna(0.0)
    rows = []

    for name, shocks in scenarios.items():
        shocked = {k: v for k, v in shocks.items() if k in returns.columns}
        if not shocked:
            continue

        # Equal-weighted proxy for "the shocked part of the market".
        driver = returns[list(shocked)].mean(axis=1)
        driver_variance = float(driver.var(ddof=1))
        average_shock = float(np.mean(list(shocked.values())))

        moves = {}
        for asset in returns.columns:
            if asset in shocked:
                moves[asset] = shocked[asset]
            elif driver_variance > 0:
                beta = float(returns[asset].cov(driver) / driver_variance)
                moves[asset] = beta * average_shock
            else:
                moves[asset] = 0.0

        impact = float(sum(aligned[a] * moves[a] for a in returns.columns))
        rows.append({
            "scenario": name,
            "portfolio_return": impact,
            **{f"{a} move": moves[a] for a in returns.columns},
        })

    return pd.DataFrame(rows).set_index("scenario") if rows else pd.DataFrame()


DEFAULT_SCENARIOS: dict[str, dict[str, float]] = {
    "Broad selloff −10%": {"SPY": -0.10},
    "Broad selloff −20%": {"SPY": -0.20},
    "Tech-led drawdown": {"NVDA": -0.25, "MSFT": -0.15, "AAPL": -0.15},
    "Melt-up +10%": {"SPY": 0.10},
}
