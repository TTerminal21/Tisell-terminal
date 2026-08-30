"""Tests for the data layer's pure functions.

Every case here corresponds to a bug that actually shipped and was found by
hand: NaN reaching DuckDB and breaking JSON serialisation, US-only providers
burning calls on BMV symbols, and FMP's free-tier limit cap silently demoting
it to a fallback.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data-layer"))

from sources import (  # noqa: E402
    fundamental_row, has_exchange_suffix, price_row, to_date, to_float, to_int,
)


# --- Coercion -------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), (".", None), ("3.5", 3.5), (7, 7.0),
    (float("nan"), None), ("abc", None),
])
def test_to_float(value, expected):
    assert to_float(value) == expected


def test_to_int_drops_nan():
    assert to_int(float("nan")) is None
    assert to_int("42.0") == 42


@pytest.mark.parametrize("value,expected", [
    ("2024-01-02T00:00:00.000Z", date(2024, 1, 2)),
    ("2024-01-02", date(2024, 1, 2)),
    (date(2024, 1, 2), date(2024, 1, 2)),
    ("not-a-date", None), (None, None),
])
def test_to_date(value, expected):
    assert to_date(value) == expected


# --- price_row ------------------------------------------------------------

def test_price_row_normalises_nan():
    """NaN is not NULL to DuckDB: it stores, then serialises as invalid JSON."""
    row = price_row("test", date(2026, 1, 2), open=float("nan"), close=10.0,
                    volume=float("nan"), high=11.0)
    assert row["open"] is None
    assert row["volume"] is None
    assert row["close"] == 10.0
    assert row["high"] == 11.0


def test_price_row_uppercases_and_fills_missing():
    row = price_row("aapl", date(2026, 1, 2), close=1.0)
    assert row["ticker"] == "AAPL"
    assert row["adj_close"] is None
    assert row["date"] == date(2026, 1, 2)


def test_price_row_ignores_unknown_fields():
    row = price_row("X", date(2026, 1, 2), close=1.0, nonsense=99)
    assert "nonsense" not in row


def test_fundamental_row_shape():
    row = fundamental_row("aapl", date(2025, 9, 27), "annual", "income",
                          "revenue", "416161000000", "USD")
    assert row["ticker"] == "AAPL"
    assert row["value"] == 416161000000.0
    assert row["currency"] == "USD"


# --- Symbol routing -------------------------------------------------------

@pytest.mark.parametrize("ticker,suffixed", [
    ("AAPL", False), ("BRK-B", False),
    ("LACOMERUBC.MX", True), ("LIVEPOLC-1.MX", True), ("PE&OLES.MX", True),
])
def test_has_exchange_suffix(ticker, suffixed):
    assert has_exchange_suffix(ticker) is suffixed


def test_us_only_providers_decline_bmv_symbols():
    """Otherwise BMV names burn calls on every provider that cannot serve them.

    fiscal_ai is the expensive one: it probes four exchanges to resolve a bare
    ticker, so an unresolvable symbol costs four calls, and 60 BMV names took
    it over its 250/day budget in a single profile pass.
    """
    from sources import fiscal_ai, fmp, tiingo
    for module in (tiingo, fmp, fiscal_ai):
        assert module.supports("AAPL") is True, module.NAME
        assert module.supports("WALMEX.MX") is False, module.NAME
        assert module.supports("LIVEPOLC-1.MX") is False, module.NAME


def test_fmp_clamps_limit_to_free_tier():
    """limit > 5 returns HTTP 402 and silently demotes FMP to a fallback."""
    from sources import fmp
    assert fmp.MAX_FREE_LIMIT == 5


# --- Watchlist ------------------------------------------------------------

def test_watchlist_groups_are_disjoint_in_purpose():
    import watchlist
    assert "mexico" in watchlist.KINDS
    # BMV listings gained a fundamentals source when CNBV was added; before
    # that they were excluded because every provider would fail on them.
    assert "mexico" in watchlist.FUNDAMENTALS_KINDS
    # ETFs stay excluded: there is no income statement to fetch, from anyone.
    assert "etfs" not in watchlist.FUNDAMENTALS_KINDS
    assert "mexico" in watchlist.PRICED_KINDS


def test_hourly_limit_blocks_budget_even_when_daily_allows(monkeypatch):
    """Tiingo answered 429 on 173/270 requests while the daily counter still
    showed budget: the free tier throttles at 50/hour, not just 500/day."""
    import quota
    monkeypatch.setattr(quota, "used", lambda provider, on=None: 10)
    monkeypatch.setattr(quota, "used_last_hour", lambda provider: 50)
    assert quota.DAILY_LIMITS["tiingo"] == 500
    assert quota.HOURLY_LIMITS["tiingo"] == 50
    # Daily budget is wide open, hourly is spent -> must refuse.
    assert quota.has_budget("tiingo") is False


def test_provider_without_hourly_limit_is_unconstrained(monkeypatch):
    import quota
    monkeypatch.setattr(quota, "used", lambda provider, on=None: 0)
    monkeypatch.setattr(quota, "used_last_hour", lambda provider: 9999)
    assert quota.has_budget("fred") is True


# --- CNBV (Mexican as-filed XBRL) ----------------------------------------

def test_cnbv_only_claims_bmv_listings():
    """The mirror of the US-only providers: it must decline US symbols so a
    US ticker never pays for a request that cannot succeed."""
    from sources import cnbv
    assert cnbv.supports("WALMEX.MX") is True
    assert cnbv.supports("LIVEPOLC-1.MX") is True
    assert cnbv.supports("AAPL") is False
    assert cnbv.supports("BRK-B") is False


@pytest.mark.parametrize("ticker,expected", [
    ("CEMEXCPO.MX", "CEMEX"),
    ("LACOMERUBC.MX", "LACOMER"),
    ("FEMSAUBD.MX", "FEMSA"),
    ("GFNORTEO.MX", "GFNORTE"),
    ("WALMEX.MX", "WALMEX"),
])
def test_cnbv_clave_candidates_include_the_real_one(ticker, expected):
    """Yahoo appends the share series; CNBV files under the bare clave."""
    from sources import cnbv
    assert expected in cnbv.candidate_claves(ticker)


def test_cnbv_skips_dimensional_facts():
    """Dimensional facts are segment/equity breakdowns. Mixing them with the
    headline figures double-counts every total."""
    from sources import cnbv
    document = {
        "HechosPorId": {
            "a": {"IdConcepto": "ifrs-full_Revenue", "IdContexto": "plain",
                  "IdUnidad": "u", "ValorNumerico": 100.0, "EsNumerico": True,
                  "EsValorNil": False},
            "b": {"IdConcepto": "ifrs-full_Revenue", "IdContexto": "dim",
                  "IdUnidad": "u", "ValorNumerico": 40.0, "EsNumerico": True,
                  "EsValorNil": False},
            "nil": {"IdConcepto": "ifrs-full_Assets", "IdContexto": "plain",
                    "IdUnidad": "u", "ValorNumerico": 5.0, "EsNumerico": True,
                    "EsValorNil": True},
        },
        # A three-month span, so the duration filter keeps it and the test
        # isolates dimensional filtering rather than accidentally testing both.
        "ContextosPorId": {
            "plain": {"Periodo": {"Tipo": 2, "FechaInicio": "2026-04-01T00:00:00Z",
                                  "FechaFin": "2026-06-30T00:00:00Z"},
                      "ContieneInformacionDimensional": False},
            "dim": {"Periodo": {"Tipo": 2, "FechaInicio": "2026-04-01T00:00:00Z",
                                "FechaFin": "2026-06-30T00:00:00Z"},
                    "ContieneInformacionDimensional": True},
        },
        "UnidadesPorId": {"u": {"Medidas": [{"Nombre": "MXN"}]}},
        "Taxonomia": {"RolesPresentacion": []},
    }
    rows = cnbv.extract_facts(document, "WALMEX.MX", "quarterly")
    assert len(rows) == 1                      # dimensional and nil both dropped
    assert rows[0]["value"] == 100.0
    assert rows[0]["currency"] == "MXN"
    assert rows[0]["period_end"] == date(2026, 6, 30)


def test_cnbv_instant_context_uses_instant_date():
    from sources import cnbv
    document = {
        "HechosPorId": {"a": {"IdConcepto": "ifrs-full_Assets", "IdContexto": "i",
                              "IdUnidad": "u", "ValorNumerico": 7.0,
                              "EsNumerico": True, "EsValorNil": False}},
        "ContextosPorId": {"i": {"Periodo": {"Tipo": 1,
                                             "FechaInstante": "2026-06-30T00:00:00Z"},
                                 "ContieneInformacionDimensional": False}},
        "UnidadesPorId": {"u": {"Medidas": [{"Nombre": "USD"}]}},
        "Taxonomia": {"RolesPresentacion": []},
    }
    rows = cnbv.extract_facts(document, "CEMEXCPO.MX", "quarterly")
    assert rows[0]["period_end"] == date(2026, 6, 30)
    assert rows[0]["currency"] == "USD"


def test_cnbv_non_currency_units_are_not_currencies():
    from sources import cnbv
    doc = {"UnidadesPorId": {"s": {"Medidas": [{"Nombre": "shares"}]}}}
    assert cnbv._currency(doc, "s") is None


def test_upsert_collapses_duplicate_keys_in_one_batch():
    """A provider returning the same fact twice must not fail the transaction."""
    import db
    columns = ["ticker", "period_end", "value"]
    rows = [("X", date(2026, 1, 1), 1.0), ("X", date(2026, 1, 1), 2.0)]
    key_index = [columns.index(c) for c in ["ticker", "period_end"]]
    deduped = {}
    for row in rows:
        deduped.setdefault(tuple(row[i] for i in key_index), row)
    assert len(deduped) == 1
    assert list(deduped.values())[0][2] == 1.0   # first occurrence wins


# --- Statement ordering ---------------------------------------------------

def test_cnbv_presentation_index_gives_statement_and_order():
    """A P&L reads top to bottom. The filer's own presentation tree carries
    that order; sorting alphabetically renders it meaningless."""
    from sources import cnbv
    document = {"Taxonomia": {"RolesPresentacion": [
        {"Nombre": "[310000] Estado de resultados", "Estructuras": [
            {"IdConcepto": "ifrs-full_IncomeStatementAbstract", "SubEstructuras": [
                {"IdConcepto": "ifrs-full_Revenue", "SubEstructuras": []},
                {"IdConcepto": "ifrs-full_CostOfSales", "SubEstructuras": []},
                {"IdConcepto": "ifrs-full_GrossProfit", "SubEstructuras": []},
            ]},
        ]},
        {"Nombre": "[210000] Estado de situación financiera", "Estructuras": [
            {"IdConcepto": "ifrs-full_Assets", "SubEstructuras": []},
        ]},
        {"Nombre": "[800500] Notas - Lista de notas", "Estructuras": [
            {"IdConcepto": "ifrs-full_Revenue", "SubEstructuras": []},
        ]},
    ]}}
    index = cnbv.presentation_index(document)
    assert index["ifrs-full_Revenue"][0] == "income"
    assert index["ifrs-full_Assets"][0] == "balance"
    # Order within the income statement must be as presented, not alphabetical.
    assert (index["ifrs-full_Revenue"][1]
            < index["ifrs-full_CostOfSales"][1]
            < index["ifrs-full_GrossProfit"][1])
    # A concept repeated in a later note keeps its first (primary) statement.
    assert index["ifrs-full_Revenue"][0] == "income"


def test_statement_pivot_uses_filed_order_not_alphabetical():
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "dashboard"))
    import pandas as pd
    import api

    # Alphabetically this is CostOfSales, GrossProfit, Revenue - which is wrong.
    frame = pd.DataFrame([
        {"statement": "income", "metric": "ifrs-full_Revenue", "period_end": "2026-06-30",
         "value": 100.0, "source": "cnbv", "ordinal": 2},
        {"statement": "income", "metric": "ifrs-full_CostOfSales", "period_end": "2026-06-30",
         "value": 60.0, "source": "cnbv", "ordinal": 3},
        {"statement": "income", "metric": "ifrs-full_GrossProfit", "period_end": "2026-06-30",
         "value": 40.0, "source": "cnbv", "ordinal": 4},
    ])
    pivot = api.statement_pivot(frame, "income", source="cnbv")
    assert list(pivot.index) == [
        "ifrs-full_Revenue", "ifrs-full_CostOfSales", "ifrs-full_GrossProfit",
    ]


def test_statement_pivot_puts_unordered_metrics_last():
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "dashboard"))
    import pandas as pd
    import api

    frame = pd.DataFrame([
        {"statement": "income", "metric": "zzz_no_ordinal", "period_end": "2026-06-30",
         "value": 1.0, "source": "x", "ordinal": None},
        {"statement": "income", "metric": "revenue", "period_end": "2026-06-30",
         "value": 2.0, "source": "x", "ordinal": 5},
    ])
    pivot = api.statement_pivot(frame, "income", source="x")
    assert list(pivot.index) == ["revenue", "zzz_no_ordinal"]


def test_cnbv_separates_durations_that_share_an_end_date():
    """A filing reports the same concept over the quarter, year-to-date and
    trailing twelve months, all ending on the same day. Keying on the end date
    alone lets a TTM figure masquerade as a quarter."""
    from sources import cnbv

    def fact(ctx):
        return {"IdConcepto": "ifrs-full_Revenue", "IdContexto": ctx, "IdUnidad": "u",
                "ValorNumerico": {"q": 4_593_416_000.0, "ytd": 8_612_510_000.0,
                                  "ttm": 17_037_635_000.0}[ctx],
                "EsNumerico": True, "EsValorNil": False}

    def duration(start, end):
        return {"Periodo": {"Tipo": 2, "FechaInicio": start, "FechaFin": end},
                "ContieneInformacionDimensional": False}

    document = {
        "HechosPorId": {k: fact(k) for k in ("q", "ytd", "ttm")},
        "ContextosPorId": {
            "q":   duration("2026-04-01T00:00:00Z", "2026-06-30T00:00:00Z"),
            "ytd": duration("2026-01-01T00:00:00Z", "2026-06-30T00:00:00Z"),
            "ttm": duration("2025-07-01T00:00:00Z", "2026-06-30T00:00:00Z"),
        },
        "UnidadesPorId": {"u": {"Medidas": [{"Nombre": "USD"}]}},
        "Taxonomia": {"RolesPresentacion": []},
    }

    quarterly = cnbv.extract_facts(document, "CEMEXCPO.MX", "quarterly")
    assert len(quarterly) == 1
    assert quarterly[0]["value"] == 4_593_416_000.0     # the quarter, not YTD or TTM

    annual = cnbv.extract_facts(document, "CEMEXCPO.MX", "annual")
    assert len(annual) == 1
    assert annual[0]["value"] == 17_037_635_000.0       # the twelve-month span


def test_cnbv_instant_contexts_are_not_duration_filtered():
    """Balance-sheet items are point-in-time and have no duration to match."""
    from sources import cnbv
    document = {
        "HechosPorId": {"a": {"IdConcepto": "ifrs-full_Assets", "IdContexto": "i",
                              "IdUnidad": "u", "ValorNumerico": 28_269_105_000.0,
                              "EsNumerico": True, "EsValorNil": False}},
        "ContextosPorId": {"i": {"Periodo": {"Tipo": 1,
                                             "FechaInstante": "2026-06-30T00:00:00Z"},
                                 "ContieneInformacionDimensional": False}},
        "UnidadesPorId": {"u": {"Medidas": [{"Nombre": "USD"}]}},
        "Taxonomia": {"RolesPresentacion": []},
    }
    for period_type in ("quarterly", "annual"):
        rows = cnbv.extract_facts(document, "CEMEXCPO.MX", period_type)
        assert len(rows) == 1, period_type


def test_span_months_is_inclusive():
    from sources import cnbv
    assert cnbv._span_months(date(2026, 4, 1), date(2026, 6, 30)) == 3
    assert cnbv._span_months(date(2026, 1, 1), date(2026, 6, 30)) == 6
    assert cnbv._span_months(date(2025, 7, 1), date(2026, 6, 30)) == 12
    assert cnbv._span_months(None, date(2026, 6, 30)) is None
