"""Provider registry and fallback chain.

Each data need - prices, fundamentals, profile, macro - has an ordered list of
providers in config.PROVIDER_ORDER. `fetch` walks that list and returns the
first usable answer, so callers ask for a capability rather than naming a
provider.

A provider is skipped without being tried when it lacks credentials or would
exceed its daily budget; it is tried and passed over when it errors. Every
attempt lands in fetch_log, so a wrong-looking number can be traced back to
whichever provider actually supplied it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import db
import quota
from config import PROVIDER_ORDER
from sources import (
    FUNDAMENTALS, MACRO, PRICES, PROFILE, NotConfigured, SourceError,
    alpaca, cnbv, fiscal_ai, fmp, fred, sec_edgar, tiingo, twelve_data,
    yfinance_source,
)

PROVIDERS = {
    module.NAME: module
    for module in (
        tiingo, fmp, fred, fiscal_ai, sec_edgar, cnbv, twelve_data, alpaca,
        yfinance_source,
    )
}

_METHOD = {
    PRICES: "fetch_prices",
    FUNDAMENTALS: "fetch_fundamentals",
    PROFILE: "fetch_profile",
    MACRO: "fetch_macro",
}


class AllSourcesFailed(SourceError):
    """Every provider for a capability was skipped or failed."""


def providers_for(capability: str) -> list[Any]:
    """Ordered, capability-filtered provider modules."""
    return [
        PROVIDERS[name]
        for name in PROVIDER_ORDER.get(capability, [])
        if name in PROVIDERS and capability in PROVIDERS[name].CAPABILITIES
    ]


def log_attempt(capability: str, target: str, provider: str, status: str,
                rows: int | None = None, message: str | None = None) -> None:
    with db.write_lock(), db.connect() as con:
        con.execute(
            """
            INSERT INTO fetch_log (run_at, capability, target, provider, status, rows, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datetime.now(timezone.utc).replace(tzinfo=None),
                capability, target, provider, status, rows,
                (message or "")[:500] or None,
            ],
        )


def fetch(
    capability: str,
    target: str,
    only: str | None = None,
    row_count: Callable[[Any], int] | None = None,
    **kwargs: Any,
) -> tuple[str, Any]:
    """Return (provider_name, result) from the first provider that answers.

    `only` pins the chain to a single provider, for comparing sources against
    each other rather than taking the first available answer.
    """
    chain = providers_for(capability)
    if only:
        chain = [module for module in chain if module.NAME == only]
        if not chain:
            raise AllSourcesFailed(
                f"{only!r} does not provide {capability!r} (or is not registered)"
            )

    if not chain:
        raise AllSourcesFailed(f"no providers registered for {capability!r}")

    method_name = _METHOD[capability]
    problems: list[str] = []

    for module in chain:
        name = module.NAME

        if not module.is_configured():
            log_attempt(capability, target, name, "skipped", None, "not configured")
            problems.append(f"{name}: not configured")
            continue

        # A provider that knows it cannot serve this symbol is skipped without
        # spending a call. Routing 50 BMV names through the US-only providers
        # would otherwise burn 50 Tiingo and 50 FMP calls a day to fail.
        if hasattr(module, "supports") and not module.supports(target):
            log_attempt(capability, target, name, "skipped", None,
                        "does not cover this listing")
            problems.append(f"{name}: does not cover this listing")
            continue

        if not quota.has_budget(name):
            message = f"daily budget exhausted ({quota.used(name)}/{quota.DAILY_LIMITS[name]})"
            log_attempt(capability, target, name, "skipped", None, message)
            problems.append(f"{name}: {message}")
            continue

        try:
            result = getattr(module, method_name)(target, **kwargs)
        except NotConfigured as exc:
            log_attempt(capability, target, name, "skipped", None, str(exc))
            problems.append(str(exc))
            continue
        except SourceError as exc:
            log_attempt(capability, target, name, "failed", None, str(exc))
            problems.append(str(exc))
            continue
        except Exception as exc:  # a provider bug must not sink the whole chain
            message = f"{type(exc).__name__}: {exc}"
            log_attempt(capability, target, name, "failed", None, message)
            problems.append(f"{name}: {message}")
            continue

        count = row_count(result) if row_count else (
            len(result) if isinstance(result, (list, tuple)) else 1
        )
        log_attempt(capability, target, name, "ok", count)
        return name, result

    raise AllSourcesFailed(
        f"all providers failed for {capability} {target!r}: " + "; ".join(problems)
    )


def status() -> list[dict[str, Any]]:
    """Which providers are usable right now, and for what."""
    return [
        {
            "provider": name,
            "capabilities": sorted(module.CAPABILITIES),
            "configured": module.is_configured(),
            "daily_limit": quota.DAILY_LIMITS.get(name),
            "used_today": quota.used(name),
            "remaining": quota.remaining(name),
        }
        for name, module in sorted(PROVIDERS.items())
    ]
