"""The personal watchlist - what a scheduled refresh actually covers.

Kept small on purpose. A full fundamentals pull costs FMP five calls per
ticker, so a 250/day free tier caps a daily-refreshed universe at roughly 20
names once prices and macro are accounted for. Expanding it is a matter of
editing watchlist.json (or POST /watchlist), not changing code.
"""
from __future__ import annotations

import json
from typing import Any

from config import WATCHLIST_PATH

# Groups exist because they need different treatment, not for tidiness:
#   equities - US listings; the only group with fundamentals coverage
#   mexico   - BMV listings. Prices and profile from yfinance; fundamentals
#              from the CNBV XBRL portal, which is the as-filed regulator
#              source and the only one with BMV coverage at all.
#   etfs     - prices and profile; no income statement exists to fetch
KINDS = ("equities", "mexico", "etfs", "macro")

# Groups whose members are worth spending a fundamentals call on. Mexican
# names go to CNBV, which is unmetered, so they do not compete for FMP budget.
FUNDAMENTALS_KINDS = ("equities", "mexico")
# Groups that get prices and a profile.
PRICED_KINDS = ("equities", "mexico", "etfs")

DEFAULT: dict[str, list[str]] = {
    "equities": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
                 "JPM", "V", "XOM", "JNJ", "WMT"],
    "etfs": ["SPY"],
    "macro": ["DGS10", "DGS2", "T10Y2Y", "FEDFUNDS",
              "CPIAUCSL", "UNRATE", "GDPC1", "DEXMXUS"],
}


def load() -> dict[str, list[str]]:
    if not WATCHLIST_PATH.exists():
        save(DEFAULT)
        return dict(DEFAULT)
    try:
        raw = json.loads(WATCHLIST_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        # A corrupt file should not stop a refresh; fall back to the default.
        return dict(DEFAULT)
    return {kind: [str(v).upper() for v in raw.get(kind, [])] for kind in KINDS}


def save(data: dict[str, list[str]]) -> dict[str, list[str]]:
    normalised = {
        kind: sorted({str(v).strip().upper() for v in data.get(kind, []) if str(v).strip()})
        for kind in KINDS
    }
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps(normalised, indent=2) + "\n")
    return normalised


def add(kind: str, items: list[str]) -> dict[str, list[str]]:
    current = load()
    current[kind] = sorted(set(current.get(kind, [])) | {i.strip().upper() for i in items})
    return save(current)


def remove(kind: str, items: list[str]) -> dict[str, list[str]]:
    current = load()
    drop = {i.strip().upper() for i in items}
    current[kind] = [i for i in current.get(kind, []) if i not in drop]
    return save(current)


def priced() -> list[str]:
    """Every symbol that should get a price refresh."""
    lists = load()
    seen: list[str] = []
    for kind in PRICED_KINDS:
        for symbol in lists.get(kind, []):
            if symbol not in seen:
                seen.append(symbol)
    return seen


def fundamentals_universe() -> list[str]:
    lists = load()
    return [s for kind in FUNDAMENTALS_KINDS for s in lists.get(kind, [])]


def estimated_daily_calls() -> dict[str, Any]:
    """Rough cost of one full refresh, to sanity-check watchlist size."""
    lists = load()
    counts = {kind: len(lists.get(kind, [])) for kind in KINDS}
    price_count = len(priced())
    fundamentals_count = len(fundamentals_universe())
    return {
        **counts,
        "priced_symbols": price_count,
        # Mexican names route straight to yfinance, which has no daily cap, so
        # they cost nothing against the metered tiers.
        "metered_price_calls": len([s for s in priced() if "." not in s]),
        "prices_calls": price_count,
        # 5 statements per ticker on FMP.
        "fundamentals_calls": fundamentals_count * 5,
        "profile_calls": price_count,
        "macro_calls": counts.get("macro", 0) * 2,  # metadata + observations
    }
