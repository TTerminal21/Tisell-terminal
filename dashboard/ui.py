"""Shared presentation components.

Kept separate from the views so every page states a number the same way. The
formatters matter more than they look: a terminal that renders 4593416000 in
one place and 4.59B in another is harder to read than one that is merely plain.
"""
from __future__ import annotations

import html
from typing import Any, Iterable

import pandas as pd
import streamlit as st

import theme


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


# --- Formatters -----------------------------------------------------------

def compact(value: float | None, currency: str | None = None,
            decimals: int = 2) -> str:
    """Scale a figure to K/M/B/T. Statements run to twelve digits."""
    if value is None or pd.isna(value):
        return "—"
    prefix = f"{currency} " if currency else ""
    magnitude = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= cutoff:
            return f"{prefix}{value / cutoff:,.{decimals}f}{suffix}"
    return f"{prefix}{value:,.{decimals}f}"


def pct(value: float | None, decimals: int = 2, signed: bool = True) -> str:
    if value is None or pd.isna(value):
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:,.{decimals}f}%"


def price(value: float | None, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.{decimals}f}"


# --- Components -----------------------------------------------------------

def badge(text: str, kind: str = "neutral") -> str:
    """Inline pill. Returns markup so several can share one st.markdown call."""
    css = {"up": "tt-badge-up", "down": "tt-badge-down",
           "warn": "tt-badge-warn", "info": "tt-badge-info"}.get(kind, "")
    return f'<span class="tt-badge {css}">{_esc(text)}</span>'


def badges(items: Iterable[tuple[str, str]]) -> None:
    markup = " ".join(badge(text, kind) for text, kind in items if text)
    if markup:
        st.markdown(markup, unsafe_allow_html=True)


def hero(symbol: str, name: str | None = None,
         facts: Iterable[str] = (), chips: Iterable[tuple[str, str]] = ()) -> None:
    """Page header: the symbol reads as the identifier it is, name beside it."""
    parts = [f'<div class="tt-hero"><span class="tt-hero-symbol">{_esc(symbol)}</span>']
    if name:
        parts.append(f'<span class="tt-hero-name">{_esc(name)}</span>')
    parts.append("</div>")

    detail = " · ".join(_esc(f) for f in facts if f)
    chip_markup = " ".join(badge(t, k) for t, k in chips if t)
    if detail or chip_markup:
        parts.append(
            f'<div class="tt-subline"><span>{detail}</span>{chip_markup}</div>'
        )
    st.markdown("".join(parts), unsafe_allow_html=True)


def section(title: str) -> None:
    """Labelled rule. Cheaper visually than a heading, and scannable."""
    st.markdown(
        f'<div class="tt-section"><span class="tt-section-title">{_esc(title)}</span>'
        f'<span class="tt-section-rule"></span></div>',
        unsafe_allow_html=True,
    )


def kpis(items: list[dict[str, Any]], columns: int | None = None) -> None:
    """A row of metrics. Each item: {label, value, delta?, help?, tone?}."""
    if not items:
        return
    for start in range(0, len(items), columns or len(items)):
        chunk = items[start:start + (columns or len(items))]
        for column, item in zip(st.columns(len(chunk)), chunk):
            column.metric(
                item["label"], item.get("value", "—"),
                item.get("delta"), help=item.get("help"),
                delta_color=item.get("tone", "normal"),
            )


def trend_chips(changes: dict[str, float | None]) -> None:
    """Period returns as coloured pills - denser than a row of metric cards."""
    items = []
    for label, value in changes.items():
        if value is None or pd.isna(value):
            items.append((f"{label} —", "neutral"))
        else:
            items.append((f"{label} {pct(value)}", "up" if value >= 0 else "down"))
    badges(items)


def styled_table(frame: pd.DataFrame, formats: dict[str, str] | None = None,
                 gradient_on: list[str] | None = None,
                 gradient_range: tuple[float, float] = (-15, 15),
                 height: int | None = None) -> None:
    """DataFrame with tabular numerals and an optional performance gradient."""
    if frame.empty:
        st.caption("Nothing to show.")
        return
    styler = frame.style.format(formats or {}, na_rep="—")
    usable = [c for c in (gradient_on or []) if c in frame.columns]
    if usable:
        styler = styler.background_gradient(
            cmap="RdYlGn", subset=usable,
            vmin=gradient_range[0], vmax=gradient_range[1],
        )
    st.dataframe(
        styler, width="stretch",
        height=height or min(700, 60 + 36 * len(frame)),
    )


def empty_state(message: str, hint: str | None = None) -> None:
    st.info(message)
    if hint:
        st.caption(hint)


def tone_for(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "off"
    return "normal"


def apply_page(title: str, icon: str = "📈") -> None:
    """set_page_config + stylesheet. Called at the top of every page."""
    st.set_page_config(page_title=f"{title} · Tisell Terminal", page_icon=icon,
                       layout="wide")
    theme.inject()
