"""Asset search shared by every view.

Searching resolves a company name to real tickers before anything is fetched,
which is the only practical way to reach non-US listings: BMV names carry an
exchange suffix you would never guess (La Comer is LACOMERUBC.MX).
"""
from __future__ import annotations

import streamlit as st

import api
import workspace

# Yahoo returns a lot of near-duplicate cross-listings; these read as noise for
# a research tool, so they sort last rather than being hidden outright.
PREFERRED_EXCHANGES = ("NMS", "NYQ", "PCX", "MEX", "NGM", "ASE")


def _rank(hit: dict) -> tuple[int, str]:
    exchange = hit.get("exchange") or ""
    return (0 if exchange in PREFERRED_EXCHANGES else 1, hit.get("symbol", ""))


def search_box(key: str, placeholder: str = "Search any company or ticker — e.g. La Comer, Walmex, NVDA") -> None:
    """Search field that opens whatever you pick into the workspace."""
    query = st.text_input(
        "Search", "", key=f"{key}_query", placeholder=placeholder,
        label_visibility="collapsed",
    ).strip()

    if not query:
        return

    payload, error = api.cached_get("/search", {"q": query})
    if error:
        st.caption(f"Search unavailable: {error}")
        return

    hits = sorted(payload.get("results", []), key=_rank)
    if not hits:
        st.caption(f"Nothing matched {query!r}.")
        return

    st.caption(f"{len(hits)} match(es) — click to open")
    for row_start in range(0, len(hits), 4):
        for column, hit in zip(st.columns(4), hits[row_start:row_start + 4]):
            with column:
                symbol = hit["symbol"]
                if st.button(
                    f"**{symbol}**",
                    key=f"{key}_hit_{symbol}",
                    help=f"{hit['name']} · {hit['exchange_label']} · {hit['type']}",
                    width="stretch",
                ):
                    workspace.open_asset(symbol)
                    st.rerun()
                st.caption(f"{hit['name'][:26]}\n\n{hit['exchange_label']}")


GROUP_LABELS = {"equities": "US equities", "mexico": "Mexico (BMV)", "etfs": "ETFs"}


def quick_open(key: str, per_group: int = 24) -> None:
    """Watchlist names as one-click chips, grouped by market.

    Capped per group: the watchlist runs to a few hundred names and a wall of
    chips is harder to use than the search box next to it.
    """
    watchlist, _ = api.cached_get("/watchlist")
    if not watchlist:
        return

    groups = [(g, watchlist.get(g, [])) for g in GROUP_LABELS if watchlist.get(g)]
    if not groups:
        return

    tabs = st.tabs([f"{GROUP_LABELS[g]} ({len(names)})" for g, names in groups])
    for tab, (group, names) in zip(tabs, groups):
        with tab:
            shown = names[:per_group]
            for row_start in range(0, len(shown), 8):
                for column, symbol in zip(st.columns(8), shown[row_start:row_start + 8]):
                    if column.button(symbol, key=f"{key}_{group}_{symbol}", width="stretch"):
                        workspace.open_asset(symbol)
                        st.rerun()
            if len(names) > per_group:
                st.caption(f"{len(names) - per_group} more — use the search box above.")
