"""Technical overlays computed from stored bars.

Deliberately plain pandas - no TA library - so the maths is visible and the
same functions can back the TUI and the analytics module later.

All of these are computed on the *adjusted* close where available: a split
would otherwise show up as a genuine price move and poison every average.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _series(frame: pd.DataFrame) -> pd.Series:
    column = "adj_close" if "adj_close" in frame and frame["adj_close"].notna().any() else "close"
    return frame[column].astype(float)


def sma(frame: pd.DataFrame, window: int) -> pd.Series:
    return _series(frame).rolling(window, min_periods=window).mean()


def ema(frame: pd.DataFrame, window: int) -> pd.Series:
    return _series(frame).ewm(span=window, adjust=False).mean()


def rsi(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = _series(frame).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    # A zero average loss means an unbroken run of gains: RSI is 100, not NaN.
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100 - 100 / (1 + rs)
    return result.where(avg_loss.ne(0.0) | avg_gain.isna(), 100.0).astype(float)


def macd(frame: pd.DataFrame, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    line = ema(frame, fast) - ema(frame, slow)
    signal_line = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"macd": line, "signal": signal_line, "histogram": line - signal_line}
    )


def bollinger(frame: pd.DataFrame, window: int = 20,
              deviations: float = 2.0) -> pd.DataFrame:
    close = _series(frame)
    middle = close.rolling(window, min_periods=window).mean()
    spread = close.rolling(window, min_periods=window).std(ddof=0) * deviations
    return pd.DataFrame(
        {"middle": middle, "upper": middle + spread, "lower": middle - spread}
    )


def returns(frame: pd.DataFrame) -> pd.Series:
    """Daily simple returns off the adjusted close."""
    return _series(frame).pct_change()


def performance(frame: pd.DataFrame) -> dict[str, float | None]:
    """Headline risk/return stats for one series. Item 7 generalises these."""
    if frame.empty or len(frame) < 2:
        return {"total_return": None, "annual_vol": None, "max_drawdown": None,
                "sharpe": None}
    daily = returns(frame).dropna()
    if daily.empty:
        return {"total_return": None, "annual_vol": None, "max_drawdown": None,
                "sharpe": None}
    close = _series(frame)
    curve = (1 + daily).cumprod()
    drawdown = (curve / curve.cummax() - 1).min()
    vol = float(daily.std(ddof=0) * (252 ** 0.5))
    mean_annual = float(daily.mean() * 252)
    return {
        "total_return": float(close.iloc[-1] / close.iloc[0] - 1),
        "annual_vol": vol,
        "max_drawdown": float(drawdown),
        # Excess-of-zero Sharpe; item 7 swaps in a real risk-free rate from FRED.
        "sharpe": (mean_annual / vol) if vol else None,
    }
