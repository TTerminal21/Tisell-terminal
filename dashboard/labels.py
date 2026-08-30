"""Human-readable names for statement line items.

Providers store raw taxonomy identifiers - `ifrs-full_ProfitLossFromOperating
Activities`, `sellingGeneralAndAdministrativeExpenses` - which are precise and
unreadable. This turns them into the words an analyst would actually use.

Two layers: a curated table for the lines people read most, and a general
fallback that strips the namespace, splits camel case and tidies the IFRS
boilerplate. The fallback means an unmapped concept still reads as English
rather than falling back to the raw id.
"""
from __future__ import annotations

import re

# Namespaces seen in the store: IFRS, the CNBV's Mexican extension, US GAAP.
_PREFIXES = (
    "ifrs-full_", "ifrs-mc_", "ifrs_mx-cor_20141205_", "ifrs_mx-cor_",
    "ifrs_", "us-gaap_", "dei_",
)

# Read most often, so worth naming by hand.
OVERRIDES: dict[str, str] = {
    # --- IFRS: income statement ---
    "ifrs-full_Revenue": "Revenue",
    "ifrs-full_CostOfSales": "Cost of sales",
    "ifrs-full_GrossProfit": "Gross profit",
    "ifrs-full_DistributionCosts": "Distribution costs",
    "ifrs-full_AdministrativeExpense": "Administrative expenses",
    "ifrs-full_OtherIncome": "Other income",
    "ifrs-full_OtherExpenseByFunction": "Other expenses",
    "ifrs-full_ProfitLossFromOperatingActivities": "Operating profit",
    "ifrs-full_FinanceIncome": "Finance income",
    "ifrs-full_FinanceCosts": "Finance costs",
    "ifrs-full_ProfitLossBeforeTax": "Profit before tax",
    "ifrs-full_IncomeTaxExpenseContinuingOperations": "Income tax expense",
    "ifrs-full_ProfitLossFromContinuingOperations": "Profit from continuing operations",
    "ifrs-full_ProfitLossFromDiscontinuedOperations": "Profit from discontinued operations",
    "ifrs-full_ProfitLoss": "Net profit",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "Attributable to owners of parent",
    "ifrs-full_ProfitLossAttributableToNoncontrollingInterests": "Attributable to non-controlling interests",
    "ifrs-full_BasicEarningsLossPerShare": "Basic EPS",
    "ifrs-full_DilutedEarningsLossPerShare": "Diluted EPS",
    "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations": "Basic EPS, continuing",
    "ifrs-full_DilutedEarningsLossPerShareFromContinuingOperations": "Diluted EPS, continuing",
    "ifrs-full_ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod":
        "Share of profit of associates & JVs",
    # --- IFRS: balance sheet ---
    "ifrs-full_Assets": "Total assets",
    "ifrs-full_CurrentAssets": "Current assets",
    "ifrs-full_NoncurrentAssets": "Non-current assets",
    "ifrs-full_Liabilities": "Total liabilities",
    "ifrs-full_CurrentLiabilities": "Current liabilities",
    "ifrs-full_NoncurrentLiabilities": "Non-current liabilities",
    "ifrs-full_Equity": "Total equity",
    "ifrs-full_EquityAttributableToOwnersOfParent": "Equity attributable to owners",
    "ifrs-full_NoncontrollingInterests": "Non-controlling interests",
    "ifrs-full_CashAndCashEquivalents": "Cash and cash equivalents",
    "ifrs-full_TradeAndOtherCurrentReceivables": "Trade and other receivables",
    "ifrs-full_Inventories": "Inventories",
    "ifrs-full_PropertyPlantAndEquipment": "Property, plant & equipment",
    "ifrs-full_IntangibleAssetsOtherThanGoodwill": "Intangible assets",
    "ifrs-full_Goodwill": "Goodwill",
    "ifrs-full_TradeAndOtherCurrentPayables": "Trade and other payables",
    "ifrs-full_RetainedEarnings": "Retained earnings",
    "ifrs-full_IssuedCapital": "Issued capital",
    # --- IFRS: cash flow ---
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "Cash flow from operations",
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": "Cash flow from investing",
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": "Cash flow from financing",
    "ifrs-full_IncreaseDecreaseInCashAndCashEquivalents": "Net change in cash",
    "ifrs-full_EffectOfExchangeRateChangesOnCashAndCashEquivalents": "FX effect on cash",
    "ifrs-full_DepreciationAndAmortisationExpense": "Depreciation & amortisation",
    # --- FMP ---
    "revenue": "Revenue",
    "costOfRevenue": "Cost of revenue",
    "grossProfit": "Gross profit",
    "researchAndDevelopmentExpenses": "R&D expenses",
    "generalAndAdministrativeExpenses": "G&A expenses",
    "sellingAndMarketingExpenses": "Selling & marketing",
    "sellingGeneralAndAdministrativeExpenses": "SG&A",
    "operatingExpenses": "Operating expenses",
    "costAndExpenses": "Total costs and expenses",
    "operatingIncome": "Operating income",
    "ebitda": "EBITDA",
    "ebit": "EBIT",
    "netIncome": "Net income",
    "incomeBeforeTax": "Pre-tax income",
    "incomeTaxExpense": "Income tax expense",
    "eps": "EPS",
    "epsDiluted": "Diluted EPS",
    "weightedAverageShsOut": "Weighted average shares",
    "weightedAverageShsOutDil": "Weighted average shares, diluted",
    "totalAssets": "Total assets",
    "totalLiabilities": "Total liabilities",
    "totalEquity": "Total equity",
    "totalDebt": "Total debt",
    "netDebt": "Net debt",
    "cashAndCashEquivalents": "Cash and cash equivalents",
    "totalCurrentAssets": "Current assets",
    "totalCurrentLiabilities": "Current liabilities",
    "freeCashFlow": "Free cash flow",
    "operatingCashFlow": "Operating cash flow",
    "capitalExpenditure": "Capital expenditure",
    "netCashProvidedByOperatingActivities": "Cash flow from operations",
    "netCashUsedForInvestingActivites": "Cash flow from investing",
    "netCashUsedProvidedByFinancingActivities": "Cash flow from financing",
    "bottomLineNetIncome": "Net income (bottom line)",
}

# IFRS suffixes that restate what the statement already tells you.
_NOISE = (
    (re.compile(r"\s*Classified As Operating Activities$", re.I), " (operating)"),
    (re.compile(r"\s*Classified As Investing Activities$", re.I), " (investing)"),
    (re.compile(r"\s*Classified As Financing Activities$", re.I), " (financing)"),
    (re.compile(r"^Adjustments For\s+", re.I), "Adj. "),
    (re.compile(r"\s*Other Than Goodwill$", re.I), ""),
)

# Words that should not be title-cased apart into letters.
_ACRONYMS = {
    "Ebitda": "EBITDA", "Ebit": "EBIT", "Eps": "EPS", "Sga": "SG&A",
    "Rd": "R&D", "Ppe": "PP&E", "Roe": "ROE", "Roa": "ROA", "Roic": "ROIC",
    "Ev": "EV", "Fx": "FX", "Jv": "JV", "Ifrs": "IFRS", "Vat": "VAT",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _strip_prefix(metric: str) -> str:
    for prefix in _PREFIXES:
        if metric.startswith(prefix):
            return metric[len(prefix):]
    return metric


def humanize(metric: str) -> str:
    """Best-effort readable name for a raw taxonomy identifier."""
    if not metric:
        return ""
    if metric in OVERRIDES:
        return OVERRIDES[metric]

    body = _strip_prefix(metric).replace("_", " ")
    text = _CAMEL.sub(" ", body).strip()
    for pattern, replacement in _NOISE:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if not text:
        return metric

    words = [_ACRONYMS.get(w.capitalize(), w) for w in text.split(" ")]
    # Sentence case: first word capitalised, the rest left as-is unless they
    # are already acronyms, so "Cash flows from used in operating activities".
    first = words[0]
    head = first if first.isupper() else first[:1].upper() + first[1:]
    tail = [w if w.isupper() else w.lower() for w in words[1:]]
    return " ".join([head] + tail)


def humanize_index(index) -> list[str]:
    """Map a pandas index of raw metric names to readable labels."""
    return [humanize(str(m)) for m in index]
