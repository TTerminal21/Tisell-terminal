"""Batch refresh across the watchlist - the scheduled half of the data layer.

Run it from a scheduler (Task Scheduler on Windows, cron elsewhere) or on
demand from the API. Order matters: prices first because they are the cheapest
and the most perishable, then macro, then profiles, then fundamentals, which
are the most expensive per ticker and change least often.

The job never lets one bad ticker stop the run, and it stops spending a
provider's budget once that provider is exhausted rather than hammering it.

    python data-layer/refresh.py                 # prices + macro (a daily run)
    python data-layer/refresh.py --full          # everything (weekly)
    python data-layer/refresh.py --what prices --since 2026-01-01
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from typing import Any

import db
import ingest
import quota
import watchlist
from sources import SourceError

# A daily run only needs recent bars; history is backfilled by an explicit
# unqualified fetch. Overlap covers holidays and late restatements.
INCREMENTAL_DAYS = 10

# FMP spends five calls per ticker on a fundamentals pull and one on a profile.
FMP_CALLS_PER_FUNDAMENTALS = 5
FMP_CALLS_PER_PROFILE = 1

# Leave headroom so a scheduled run never consumes the whole day's budget and
# locks you out of ad-hoc fetches in the dashboard.
BUDGET_RESERVE = 40


def _stale_first(table: str, candidates: list[str], column: str = "ticker") -> list[str]:
    """Order symbols by how long ago they were last stored, never-stored first.

    A watchlist of 200+ names cannot be fully refreshed inside a 250-call/day
    free tier, so runs rotate: each one takes the most out-of-date slice that
    fits in the remaining budget, and over a few days everything comes current.
    """
    if not candidates:
        return []
    placeholders = ", ".join("?" for _ in candidates)
    with db.connect() as con:
        rows = con.execute(
            f"""SELECT {column}, MAX(fetched_at) FROM {table}
                WHERE {column} IN ({placeholders}) GROUP BY {column}""",
            candidates,
        ).fetchall()
    last_seen = {ticker: stamp for ticker, stamp in rows}
    # Never-fetched sort first; among the rest, oldest first.
    return sorted(candidates, key=lambda t: (last_seen.get(t) is not None,
                                             last_seen.get(t) or datetime.min))


def _affordable(count_wanted: int, calls_each: int, provider: str = "fmp") -> int:
    """How many targets fit in what is left of a provider's daily budget."""
    remaining = quota.remaining(provider)
    if remaining is None:
        return count_wanted
    usable = max(0, remaining - BUDGET_RESERVE)
    return min(count_wanted, usable // calls_each)


def _incremental_start() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=INCREMENTAL_DAYS)


def refresh(
    what: list[str] | None = None,
    since: date | None = None,
    period_type: str = "annual",
    full_history: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Refresh the watchlist. Returns a per-item report."""
    wanted = what or ["prices", "macro"]
    lists = watchlist.load()
    started = datetime.now(timezone.utc)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    deferred: list[str] = []

    def run(label: str, target: str, fn, **kwargs) -> None:
        try:
            outcome = fn(target, **kwargs)
        except SourceError as exc:
            errors.append({"capability": label, "target": target, "error": str(exc)})
        except Exception as exc:  # keep the batch alive
            errors.append(
                {"capability": label, "target": target,
                 "error": f"{type(exc).__name__}: {exc}"}
            )
        else:
            results.append(outcome)

    start = None if full_history else (since or _incremental_start())

    priced = watchlist.priced()

    if "prices" in wanted:
        for ticker in priced:
            run("prices", ticker, ingest.ingest_prices, start=start)

    if "macro" in wanted:
        for series_id in lists["macro"]:
            run("macro", series_id, ingest.ingest_macro, start=since)

    if "profile" in wanted:
        queue = _stale_first("profiles", priced)
        # Non-US names are served by yfinance, which is not metered, so only
        # the US slice competes for FMP's budget.
        metered = [t for t in queue if "." not in t]
        free = [t for t in queue if "." in t]
        allowed = _affordable(len(metered), FMP_CALLS_PER_PROFILE)
        if limit is not None:
            metered, free = metered[:limit], free[:limit]
        for ticker in free + metered[:allowed]:
            run("profile", ticker, ingest.ingest_profile)

    # Only US equities: ETFs have no statements, and BMV listings are answered
    # with HTTP 402 by FMP's free tier and cannot be resolved by Fiscal.ai.
    if "fundamentals" in wanted:
        queue = _stale_first("fundamentals", watchlist.fundamentals_universe())
        # BMV names are served by CNBV, which is an unmetered public regulator
        # portal, so they never compete for FMP's budget.
        free = [t for t in queue if "." in t]
        metered = [t for t in queue if "." not in t]
        allowed = _affordable(len(metered), FMP_CALLS_PER_FUNDAMENTALS)
        if limit is not None:
            allowed = min(allowed, limit)
            free = free[:limit]
        skipped = len(metered) - allowed
        for ticker in free + metered[:allowed]:
            run("fundamentals", ticker, ingest.ingest_fundamentals,
                period_type=period_type)
        if skipped > 0:
            deferred.append(
                f"fundamentals: {skipped} US ticker(s) deferred to a later run - "
                f"FMP budget allows {allowed} today. BMV names are unaffected."
            )

    finished = datetime.now(timezone.utc)
    return {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "seconds": round((finished - started).total_seconds(), 1),
        "requested": wanted,
        "ok": len(results),
        "failed": len(errors),
        "rows_stored": sum(r.get("rows_stored", 0) for r in results),
        "providers_used": sorted({r["provider"] for r in results}),
        "results": results,
        "errors": errors,
        "deferred": deferred,
        "quota": quota.report(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--what", nargs="+", default=None,
        choices=["prices", "macro", "profile", "fundamentals"],
        help="default: prices macro",
    )
    parser.add_argument("--full", action="store_true",
                        help="all four capabilities (weekly run)")
    parser.add_argument("--since", help="YYYY-MM-DD; overrides the incremental window")
    parser.add_argument("--full-history", action="store_true",
                        help="ignore the incremental window and backfill everything")
    parser.add_argument("--period", default="annual", choices=["annual", "quarterly"])
    parser.add_argument("--limit", type=int,
                        help="cap how many targets per capability this run touches")
    args = parser.parse_args()

    wanted = ["prices", "macro", "profile", "fundamentals"] if args.full else args.what
    report = refresh(
        what=wanted,
        since=date.fromisoformat(args.since) if args.since else None,
        period_type=args.period,
        full_history=args.full_history,
        limit=args.limit,
    )

    print(
        f"refresh: {report['ok']} ok, {report['failed']} failed, "
        f"{report['rows_stored']:,} rows in {report['seconds']}s "
        f"via {', '.join(report['providers_used']) or 'nothing'}"
    )
    for note in report["deferred"]:
        print(f"  {note}")
    for failure in report["errors"]:
        print(f"  FAILED {failure['capability']} {failure['target']}: {failure['error'][:160]}")
    for row in report["quota"]:
        if row["limit"] is not None and row["used"]:
            print(f"  quota {row['provider']}: {row['used']}/{row['limit']}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
