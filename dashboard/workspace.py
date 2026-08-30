"""Open-asset workspace.

The terminal keeps several assets open at once, the way a browser keeps tabs,
rather than making you re-pick one name at a time. The open set lives in
session state and is shared by every view, so opening a name on Overview also
opens it on Charts and Fundamentals.
"""
from __future__ import annotations

import streamlit as st

STATE_KEY = "open_assets"
ACTIVE_KEY = "active_asset"
MAX_OPEN = 8


def _assets() -> list[str]:
    if STATE_KEY not in st.session_state:
        st.session_state[STATE_KEY] = []
    return st.session_state[STATE_KEY]


def open_assets() -> list[str]:
    return list(_assets())


def is_open(symbol: str) -> bool:
    return symbol.strip().upper() in _assets()


def open_asset(symbol: str, activate: bool = True) -> None:
    """Add a symbol to the workspace, oldest dropped once MAX_OPEN is hit."""
    ticker = symbol.strip().upper()
    if not ticker:
        return
    assets = _assets()
    if ticker not in assets:
        assets.append(ticker)
        # Cap the set so a long session does not end up with 40 tabs.
        while len(assets) > MAX_OPEN:
            dropped = assets.pop(0)
            if st.session_state.get(ACTIVE_KEY) == dropped:
                st.session_state[ACTIVE_KEY] = assets[0] if assets else None
    if activate:
        st.session_state[ACTIVE_KEY] = ticker


def close_asset(symbol: str) -> None:
    ticker = symbol.strip().upper()
    assets = _assets()
    if ticker in assets:
        assets.remove(ticker)
    if st.session_state.get(ACTIVE_KEY) == ticker:
        st.session_state[ACTIVE_KEY] = assets[-1] if assets else None


def close_all() -> None:
    st.session_state[STATE_KEY] = []
    st.session_state[ACTIVE_KEY] = None


def active() -> str | None:
    assets = _assets()
    current = st.session_state.get(ACTIVE_KEY)
    if current in assets:
        return current
    return assets[-1] if assets else None


def set_active(symbol: str) -> None:
    if symbol.strip().upper() in _assets():
        st.session_state[ACTIVE_KEY] = symbol.strip().upper()


def selector(key: str, label: str = "Open assets") -> str | None:
    """Render the workspace as a horizontal picker and return the active one.

    st.tabs cannot be driven from state, so this uses a radio styled as a row
    of pills - it keeps the active asset stable when you switch views.
    """
    assets = open_assets()
    if not assets:
        return None

    current = active()
    index = assets.index(current) if current in assets else 0

    row, close_col = st.columns([9, 1])
    with row:
        chosen = st.radio(
            label, assets, index=index, horizontal=True,
            key=f"{key}_ws", label_visibility="collapsed",
        )
    with close_col:
        if st.button("✕", key=f"{key}_close", help=f"Close {chosen}"):
            close_asset(chosen)
            st.rerun()

    if chosen != current:
        set_active(chosen)
    return chosen
