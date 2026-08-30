"""Option pricing: Black-Scholes closed form, Greeks, and Monte Carlo.

Conventions used throughout:
  S  spot            K  strike            T  years to expiry
  r  risk-free (cc)  q  dividend yield    sigma  annualised vol
All rates are continuously compounded decimals (0.05 = 5%).

Greeks are returned in *raw* units - per 1.00 of the underlying, per 1.00 of
vol, per 1.0 year. The dashboard scales vega and theta for display; keeping the
raw form here means the portfolio module can aggregate them without guessing
what scaling was already applied.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Literal

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float,
           q: float = 0.0) -> tuple[float, float]:
    vol_time = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_time
    return d1, d1 - vol_time


def _intrinsic(S: float, K: float, T: float, r: float, q: float,
               option: OptionType) -> Greeks:
    """Degenerate case: no time or no vol left, so the option is its forward payoff."""
    discounted_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)
    if option == "call":
        price = max(discounted_spot - discounted_strike, 0.0)
        delta = 1.0 if discounted_spot > discounted_strike else 0.0
    else:
        price = max(discounted_strike - discounted_spot, 0.0)
        delta = -1.0 if discounted_strike > discounted_spot else 0.0
    return Greeks(price, delta, 0.0, 0.0, 0.0, 0.0)


def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                  q: float = 0.0, option: OptionType = "call") -> Greeks:
    """Price and Greeks for a European option."""
    if S <= 0 or K <= 0:
        raise ValueError("spot and strike must be positive")
    if T <= 0 or sigma <= 0:
        return _intrinsic(S, K, max(T, 0.0), r, q, option)

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    sqrt_T = math.sqrt(T)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)
    pdf_d1 = norm.pdf(d1)

    # Shared across both payoffs.
    gamma = disc_q * pdf_d1 / (S * sigma * sqrt_T)
    vega = S * disc_q * pdf_d1 * sqrt_T

    if option == "call":
        price = S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
        delta = disc_q * norm.cdf(d1)
        theta = (
            -S * disc_q * pdf_d1 * sigma / (2 * sqrt_T)
            - r * K * disc_r * norm.cdf(d2)
            + q * S * disc_q * norm.cdf(d1)
        )
        rho = K * T * disc_r * norm.cdf(d2)
    elif option == "put":
        price = K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)
        delta = -disc_q * norm.cdf(-d1)
        theta = (
            -S * disc_q * pdf_d1 * sigma / (2 * sqrt_T)
            + r * K * disc_r * norm.cdf(-d2)
            - q * S * disc_q * norm.cdf(-d1)
        )
        rho = -K * T * disc_r * norm.cdf(-d2)
    else:
        raise ValueError(f"option must be 'call' or 'put', got {option!r}")

    return Greeks(float(price), float(delta), float(gamma), float(vega),
                  float(theta), float(rho))


def implied_vol(price: float, S: float, K: float, T: float, r: float,
                q: float = 0.0, option: OptionType = "call",
                bounds: tuple[float, float] = (1e-6, 5.0)) -> float | None:
    """Back out the vol that reproduces an observed price. None if unattainable."""
    if T <= 0 or price <= 0:
        return None

    # Outside the no-arbitrage band no positive vol can reach this price.
    floor = _intrinsic(S, K, T, r, q, option).price
    if price < floor - 1e-9:
        return None

    def objective(sigma: float) -> float:
        return black_scholes(S, K, T, r, sigma, q, option).price - price

    low, high = bounds
    try:
        if objective(low) * objective(high) > 0:
            return None
        return float(brentq(objective, low, high, maxiter=200, xtol=1e-10))
    except (ValueError, RuntimeError):
        return None


def monte_carlo(S: float, K: float, T: float, r: float, sigma: float,
                q: float = 0.0, option: OptionType = "call",
                paths: int = 100_000, seed: int | None = 42,
                antithetic: bool = True) -> dict[str, Any]:
    """Terminal-value Monte Carlo under GBM, with a standard error.

    Antithetic sampling halves the number of independent draws needed for a
    given error, which matters because the whole point of the standard error
    here is to show whether the simulation has actually converged on the
    closed-form answer.
    """
    if T <= 0 or sigma <= 0:
        exact = _intrinsic(S, K, max(T, 0.0), r, q, option)
        return {"price": exact.price, "std_error": 0.0, "paths": 0,
                "closed_form": exact.price, "difference": 0.0}

    rng = np.random.default_rng(seed)
    draws = paths // 2 if antithetic else paths
    normals = rng.standard_normal(draws)
    if antithetic:
        normals = np.concatenate([normals, -normals])

    drift = (r - q - 0.5 * sigma * sigma) * T
    diffusion = sigma * math.sqrt(T)
    terminal = S * np.exp(drift + diffusion * normals)

    payoff = np.maximum(terminal - K, 0.0) if option == "call" else np.maximum(K - terminal, 0.0)
    discounted = math.exp(-r * T) * payoff

    price = float(discounted.mean())
    std_error = float(discounted.std(ddof=1) / math.sqrt(discounted.size))
    closed = black_scholes(S, K, T, r, sigma, q, option).price

    return {
        "price": price,
        "std_error": std_error,
        "ci_low": price - 1.96 * std_error,
        "ci_high": price + 1.96 * std_error,
        "paths": int(discounted.size),
        "closed_form": closed,
        "difference": price - closed,
    }


def simulate_paths(S: float, T: float, r: float, sigma: float, q: float = 0.0,
                   paths: int = 200, steps: int = 252,
                   seed: int | None = 42) -> np.ndarray:
    """Full GBM paths, shape (paths, steps + 1). For plotting, not pricing."""
    rng = np.random.default_rng(seed)
    dt = T / steps
    shocks = rng.standard_normal((paths, steps))
    increments = (r - q - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * shocks
    log_paths = np.concatenate(
        [np.zeros((paths, 1)), np.cumsum(increments, axis=1)], axis=1
    )
    return S * np.exp(log_paths)
