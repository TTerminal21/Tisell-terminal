"""Discounted cash flow, driven by stored fundamentals.

Deliberately a plain two-stage model: explicit free-cash-flow forecast for N
years, then a Gordon-growth terminal value, everything discounted at WACC.
The output carries the terminal value's share of enterprise value because that
number is the honest health warning on any DCF - when it is 80%+, the
valuation is a statement about the terminal assumptions, not about the forecast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DCFInputs:
    free_cash_flow: float          # most recent annual FCF, currency units
    growth_rate: float             # annual FCF growth over the forecast window
    terminal_growth: float         # perpetual growth after it
    discount_rate: float           # WACC
    years: int = 5
    net_debt: float = 0.0          # total debt - cash; subtracted from EV
    shares_outstanding: float = 0.0


@dataclass
class DCFResult:
    enterprise_value: float
    equity_value: float
    value_per_share: float | None
    terminal_value: float
    terminal_pv: float
    terminal_share: float          # fraction of EV coming from terminal value
    projections: list[dict[str, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enterprise_value": self.enterprise_value,
            "equity_value": self.equity_value,
            "value_per_share": self.value_per_share,
            "terminal_value": self.terminal_value,
            "terminal_pv": self.terminal_pv,
            "terminal_share": self.terminal_share,
            "projections": self.projections,
            "warnings": self.warnings,
        }


def run_dcf(inputs: DCFInputs) -> DCFResult:
    """Two-stage DCF. Raises only on assumptions that make the model undefined."""
    if inputs.discount_rate <= inputs.terminal_growth:
        raise ValueError(
            "discount rate must exceed terminal growth, or the terminal value "
            "is infinite (Gordon growth breaks down)"
        )
    if inputs.years < 1:
        raise ValueError("forecast window must be at least one year")

    warnings: list[str] = []
    if inputs.free_cash_flow <= 0:
        warnings.append(
            "Base free cash flow is zero or negative — a growth-discount model "
            "cannot value this meaningfully."
        )
    if inputs.terminal_growth > 0.04:
        warnings.append(
            f"Terminal growth of {inputs.terminal_growth:.1%} exceeds long-run "
            "GDP growth; the model assumes the firm outgrows the economy forever."
        )

    projections: list[dict[str, float]] = []
    pv_explicit = 0.0
    cash_flow = inputs.free_cash_flow

    for year in range(1, inputs.years + 1):
        cash_flow = cash_flow * (1 + inputs.growth_rate)
        discount_factor = (1 + inputs.discount_rate) ** year
        present_value = cash_flow / discount_factor
        pv_explicit += present_value
        projections.append({
            "year": year,
            "free_cash_flow": cash_flow,
            "discount_factor": 1 / discount_factor,
            "present_value": present_value,
        })

    final_flow = projections[-1]["free_cash_flow"]
    terminal_value = (
        final_flow * (1 + inputs.terminal_growth)
        / (inputs.discount_rate - inputs.terminal_growth)
    )
    terminal_pv = terminal_value / ((1 + inputs.discount_rate) ** inputs.years)

    enterprise_value = pv_explicit + terminal_pv
    equity_value = enterprise_value - inputs.net_debt
    per_share = (
        equity_value / inputs.shares_outstanding
        if inputs.shares_outstanding else None
    )

    terminal_share = terminal_pv / enterprise_value if enterprise_value else 0.0
    if terminal_share > 0.80:
        warnings.append(
            f"{terminal_share:.0%} of enterprise value sits in the terminal "
            "value — the result reflects the perpetuity assumptions far more "
            "than the explicit forecast."
        )

    return DCFResult(
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        value_per_share=per_share,
        terminal_value=terminal_value,
        terminal_pv=terminal_pv,
        terminal_share=terminal_share,
        projections=projections,
        warnings=warnings,
    )


def sensitivity(inputs: DCFInputs, discount_rates: list[float],
                terminal_growths: list[float]) -> list[dict[str, Any]]:
    """Value-per-share grid across WACC and terminal growth.

    A DCF's output range across plausible assumptions is more informative than
    any single point estimate, so this is a first-class output rather than an
    afterthought.
    """
    grid = []
    for rate in discount_rates:
        row: dict[str, Any] = {"discount_rate": rate}
        for growth in terminal_growths:
            key = f"{growth:.2%}"
            if rate <= growth:
                row[key] = None
                continue
            trial = DCFInputs(
                free_cash_flow=inputs.free_cash_flow,
                growth_rate=inputs.growth_rate,
                terminal_growth=growth,
                discount_rate=rate,
                years=inputs.years,
                net_debt=inputs.net_debt,
                shares_outstanding=inputs.shares_outstanding,
            )
            result = run_dcf(trial)
            row[key] = result.value_per_share if result.value_per_share else result.equity_value
        grid.append(row)
    return grid


def wacc(equity_value: float, debt_value: float, cost_of_equity: float,
         cost_of_debt: float, tax_rate: float) -> float:
    """Standard weighted average cost of capital."""
    total = equity_value + debt_value
    if total <= 0:
        raise ValueError("equity + debt must be positive")
    equity_weight = equity_value / total
    debt_weight = debt_value / total
    return equity_weight * cost_of_equity + debt_weight * cost_of_debt * (1 - tax_rate)


def capm_cost_of_equity(risk_free: float, beta: float,
                        equity_risk_premium: float = 0.05) -> float:
    return risk_free + beta * equity_risk_premium
