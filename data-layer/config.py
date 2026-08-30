"""Configuration for the data layer, read from the repo-root .env file.

Keys never appear in code. Copy .env.example to .env and fill it in.
A provider with no key configured is skipped by the fallback chain rather than
being tried and failing, so a partly-filled .env is a supported state.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

DATA_LAYER_DIR = Path(__file__).resolve().parent
REPO_ROOT = DATA_LAYER_DIR.parent

load_dotenv(REPO_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _path_or_default(var: str, default: Path) -> Path:
    raw = _env(var)
    return Path(raw).expanduser() if raw else default


# --- Provider credentials -------------------------------------------------
TIINGO_API_KEY = _env("TIINGO_API_KEY")
FMP_API_KEY = _env("FMP_API_KEY")
FRED_API_KEY = _env("FRED_API_KEY")
FISCAL_AI_API_KEY = _env("FISCAL_AI_API_KEY")
TWELVE_DATA_API_KEY = _env("TWELVE_DATA_API_KEY")
ALPACA_API_KEY_ID = _env("ALPACA_API_KEY_ID")
ALPACA_API_SECRET_KEY = _env("ALPACA_API_SECRET_KEY")

# SEC requires a descriptive User-Agent naming a real contact, or it returns 403.
SEC_EDGAR_USER_AGENT = _env("SEC_EDGAR_USER_AGENT")

# --- Data-layer auth ------------------------------------------------------
# Shared secret between the data layer and its clients. Blank disables the
# check, so an existing local setup keeps working; set it once you run any
# piece on another device.
DATA_LAYER_API_KEY = _env("DATA_LAYER_API_KEY")
API_KEY_HEADER = "X-API-Key"

# --- Storage / wiring -----------------------------------------------------
DUCKDB_PATH = _path_or_default("DUCKDB_PATH", DATA_LAYER_DIR / "terminal.duckdb")
WATCHLIST_PATH = _path_or_default("WATCHLIST_PATH", DATA_LAYER_DIR / "watchlist.json")
DATA_LAYER_URL = _env("DATA_LAYER_URL", "http://127.0.0.1:8000").rstrip("/")

# --- Fallback order per capability ---------------------------------------
# First provider that is configured, under quota, and answers wins. Tiingo
# leads prices because it is an official API with a generous EOD allowance;
# yfinance sits last as the unofficial catch-all the brief warns about.
PROVIDER_ORDER: dict[str, list[str]] = {
    "prices": ["tiingo", "fmp", "twelve_data", "alpaca", "yfinance"],
    # cnbv leads because it is the only source with BMV coverage and it
    # declines US symbols outright, so it costs a US ticker nothing.
    "fundamentals": ["cnbv", "fmp", "fiscal_ai", "sec_edgar", "yfinance"],
    "profile": ["fmp", "fiscal_ai", "yfinance"],
    "macro": ["fred"],
}
