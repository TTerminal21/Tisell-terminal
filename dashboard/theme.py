"""Visual theme: palette tokens and the stylesheet.

Design position: the chrome is deliberately quiet and the data is what carries
colour. Green and red mean "up" and "down" and are never used for buttons,
headers or borders, so a red number always means a red number.

Streamlit's internal class names change between versions, so every selector
here is either a documented `data-testid` or a plain element selector. If one
does break the page still renders - it just loses that flourish - which is why
nothing structural is expressed in CSS.
"""
from __future__ import annotations

import streamlit as st

# --- Tokens ---------------------------------------------------------------

LIGHT = {
    "bg": "#FFFFFF", "surface": "#F5F7FA", "surface_alt": "#EEF2F7",
    "border": "#DFE5EC", "border_strong": "#C7D0DB",
    "text": "#111827", "muted": "#5B6675", "faint": "#8A94A3",
    "primary": "#2E6BE6", "primary_soft": "#E8F0FE",
    "up": "#0E9F6E", "down": "#E02424",
    "up_soft": "#E7F6F0", "down_soft": "#FDECEC",
    "warn": "#B45309", "warn_soft": "#FEF3C7",
    "grid": "#F0F3FA", "plot_bg": "#FFFFFF",
}

DARK = {
    "bg": "#0E1117", "surface": "#161B22", "surface_alt": "#1C2229",
    "border": "#2A313A", "border_strong": "#3A424D",
    "text": "#E6EAF0", "muted": "#9BA6B4", "faint": "#6E7A88",
    "primary": "#5B8DEF", "primary_soft": "#16233A",
    "up": "#3FB950", "down": "#F85149",
    "up_soft": "#12261A", "down_soft": "#2A1416",
    "warn": "#D29922", "warn_soft": "#2B2410",
    "grid": "#20262E", "plot_bg": "#0E1117",
}

# Numerals must be tabular or columns of figures do not line up, which is the
# single most legible-looking thing a financial UI can do.
NUMERIC_STACK = ('ui-monospace, "SF Mono", "JetBrains Mono", "Roboto Mono", '
                 'Menlo, Consolas, monospace')


def palette() -> dict[str, str]:
    """Tokens matching what Streamlit actually painted.

    Order matters. `st.context.theme.type` reports the *browser's*
    prefers-color-scheme, which is not necessarily what Streamlit rendered: a
    `base` in config.toml wins over it, and then the tokens here would go dark
    while the chrome stayed light. The configured base is therefore
    authoritative, and the browser preference is only a fallback for when no
    base is configured.
    """
    configured = st.get_option("theme.base")
    if configured:
        return DARK if str(configured).lower() == "dark" else LIGHT
    try:
        kind = st.context.theme.type
    except Exception:
        kind = None
    return DARK if str(kind or "light").lower() == "dark" else LIGHT


def _stylesheet(p: dict[str, str]) -> str:
    return f"""
    <style>
      :root {{
        --tt-bg: {p['bg']};
        --tt-surface: {p['surface']};
        --tt-surface-alt: {p['surface_alt']};
        --tt-border: {p['border']};
        --tt-border-strong: {p['border_strong']};
        --tt-text: {p['text']};
        --tt-muted: {p['muted']};
        --tt-faint: {p['faint']};
        --tt-primary: {p['primary']};
        --tt-primary-soft: {p['primary_soft']};
        --tt-up: {p['up']};
        --tt-down: {p['down']};
        --tt-up-soft: {p['up_soft']};
        --tt-down-soft: {p['down_soft']};
        --tt-warn: {p['warn']};
        --tt-warn-soft: {p['warn_soft']};
        --tt-mono: {NUMERIC_STACK};
      }}

      /* --- Typography ------------------------------------------------- */
      html, body, [class*="st-"] {{
        font-feature-settings: "cv02","cv03","cv04","cv11";
      }}
      h1, h2, h3 {{ letter-spacing: -0.018em; }}
      h1 {{ font-weight: 680; font-size: 1.85rem; }}
      h2 {{ font-weight: 640; font-size: 1.30rem; margin-top: 0.4rem; }}
      h3 {{ font-weight: 620; font-size: 1.05rem; color: var(--tt-muted);
            text-transform: uppercase; letter-spacing: 0.05em; }}

      /* Tighten Streamlit's very generous default page padding. */
      .block-container {{ padding-top: 2.4rem; padding-bottom: 3rem; max-width: 1500px; }}

      /* --- Numerals --------------------------------------------------- */
      [data-testid="stMetricValue"],
      [data-testid="stMetricDelta"],
      [data-testid="stDataFrame"] {{
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum";
      }}
      [data-testid="stMetricValue"] {{
        font-family: var(--tt-mono);
        font-size: 1.55rem; font-weight: 620; letter-spacing: -0.02em;
      }}
      [data-testid="stMetricLabel"] {{
        color: var(--tt-muted); font-size: 0.76rem; font-weight: 560;
        text-transform: uppercase; letter-spacing: 0.06em;
      }}
      [data-testid="stMetricDelta"] {{ font-size: 0.82rem; font-weight: 560; }}

      /* Metrics read as cards rather than floating text. */
      [data-testid="stMetric"] {{
        background: var(--tt-surface);
        border: 1px solid var(--tt-border);
        border-radius: 10px;
        padding: 0.7rem 0.9rem 0.75rem;
      }}

      /* --- Tabs ------------------------------------------------------- */
      [data-testid="stTabs"] [role="tablist"] {{
        gap: 0.15rem; border-bottom: 1px solid var(--tt-border);
      }}
      [data-testid="stTabs"] [role="tab"] {{
        font-size: 0.88rem; font-weight: 560; color: var(--tt-muted);
        padding: 0.42rem 0.85rem; border-radius: 8px 8px 0 0;
      }}
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
        color: var(--tt-primary); background: var(--tt-primary-soft);
      }}

      /* --- Tables ----------------------------------------------------- */
      [data-testid="stDataFrame"] {{
        border: 1px solid var(--tt-border); border-radius: 10px; overflow: hidden;
      }}

      /* --- Inputs & buttons ------------------------------------------- */
      [data-testid="stTextInput"] input {{ font-size: 0.92rem; }}
      .stButton button {{
        font-weight: 560; border-radius: 8px; font-size: 0.87rem;
        border: 1px solid var(--tt-border-strong);
      }}
      .stButton button:hover {{ border-color: var(--tt-primary); color: var(--tt-primary); }}

      /* --- Sidebar ---------------------------------------------------- */
      [data-testid="stSidebar"] {{ border-right: 1px solid var(--tt-border); }}
      [data-testid="stSidebar"] h2 {{ font-size: 1.0rem; }}

      /* --- Custom components ------------------------------------------ */
      .tt-hero {{
        display: flex; align-items: baseline; gap: 0.65rem; flex-wrap: wrap;
        margin: 0 0 0.15rem;
      }}
      .tt-hero-symbol {{
        font-family: var(--tt-mono); font-size: 1.85rem; font-weight: 680;
        letter-spacing: -0.02em; color: var(--tt-text);
      }}
      .tt-hero-name {{ font-size: 1.05rem; color: var(--tt-muted); font-weight: 500; }}
      .tt-subline {{
        color: var(--tt-faint); font-size: 0.83rem; margin-bottom: 0.9rem;
        display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;
      }}

      .tt-badge {{
        display: inline-flex; align-items: center; gap: 0.3rem;
        font-size: 0.71rem; font-weight: 600; letter-spacing: 0.035em;
        text-transform: uppercase; padding: 0.16rem 0.5rem; border-radius: 999px;
        border: 1px solid var(--tt-border); background: var(--tt-surface);
        color: var(--tt-muted); white-space: nowrap;
      }}
      .tt-badge-up {{ color: var(--tt-up); background: var(--tt-up-soft);
                      border-color: transparent; }}
      .tt-badge-down {{ color: var(--tt-down); background: var(--tt-down-soft);
                        border-color: transparent; }}
      .tt-badge-warn {{ color: var(--tt-warn); background: var(--tt-warn-soft);
                        border-color: transparent; }}
      .tt-badge-info {{ color: var(--tt-primary); background: var(--tt-primary-soft);
                        border-color: transparent; }}

      .tt-section {{
        display: flex; align-items: center; gap: 0.6rem;
        margin: 1.5rem 0 0.6rem;
      }}
      .tt-section-title {{
        font-size: 0.78rem; font-weight: 660; text-transform: uppercase;
        letter-spacing: 0.08em; color: var(--tt-muted); white-space: nowrap;
      }}
      .tt-section-rule {{ flex: 1; height: 1px; background: var(--tt-border); }}

      .tt-num {{ font-family: var(--tt-mono); font-variant-numeric: tabular-nums; }}
      .tt-up {{ color: var(--tt-up); }}
      .tt-down {{ color: var(--tt-down); }}
      .tt-muted {{ color: var(--tt-muted); }}
    </style>
    """


def inject() -> None:
    """Apply the stylesheet. Called once per page, after set_page_config."""
    st.markdown(_stylesheet(palette()), unsafe_allow_html=True)
