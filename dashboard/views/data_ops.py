"""Data operations: quota, providers, refresh, watchlist, fetch log.

The only page that spends provider quota.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import api

health = api.require_backend()

st.header("Data layer")

cols = st.columns(6)
cols[0].metric("Tickers", f"{health['tickers']:,}")
cols[1].metric("Price bars", f"{health['prices']:,}")
cols[2].metric("Fundamentals", f"{health['fundamentals']:,}")
cols[3].metric("Profiles", f"{health['profiles']:,}")
cols[4].metric("Macro series", f"{health['macro_series']:,}")
cols[5].metric("Macro points", f"{health['macro_observations']:,}")
st.caption(f"DuckDB: `{health['duckdb_path']}`")

st.divider()

# --- Quota ----------------------------------------------------------------

st.subheader("API quota")
quota, quota_error = api.get("/quota")
if quota_error:
    st.warning(quota_error)
else:
    st.caption(f"Resets at {quota['resets_at']} (UTC midnight).")
    st.caption(
        "Some tiers throttle per hour as well, and during a bulk backfill that "
        "is the binding constraint long before the daily cap."
    )
    for row in quota["providers"]:
        left, right = st.columns([3, 7])
        limit = row["limit"]
        left.write(
            f"**{row['provider']}** — {row['used']:,}"
            + (f" / {limit:,}" if limit else " (no cap)")
        )
        with right:
            if limit:
                used_fraction = min(1.0, row["used"] / limit)
                st.progress(
                    used_fraction,
                    text=f"{row['remaining']:,} left today"
                         + ("  ⚠️ low" if used_fraction > 0.8 else ""),
                )
            else:
                st.caption("no published daily limit")

            hourly_limit = row.get("hourly_limit")
            if hourly_limit:
                hourly_used = row.get("hourly_used") or 0
                throttled = (row.get("hourly_remaining") or 0) <= 0
                st.caption(
                    f"hourly: {hourly_used:,} / {hourly_limit:,} in the last hour"
                    + ("  🚫 throttled — requests fall through to the next provider"
                       if throttled else "")
                )

st.divider()

# --- Providers ------------------------------------------------------------

st.subheader("Providers & fallback order")
providers, providers_error = api.get("/providers")
if providers_error:
    st.warning(providers_error)
else:
    chains = providers["chains"]
    for capability, chain in chains.items():
        configured = {p["provider"] for p in providers["providers"] if p["configured"]}
        rendered = " → ".join(
            f"**{name}**" if name in configured else f"~~{name}~~" for name in chain
        )
        st.write(f"`{capability}` — {rendered}")
    st.caption("Struck-through providers have no credentials and are skipped.")

    frame = pd.DataFrame(providers["providers"])
    frame["capabilities"] = frame["capabilities"].apply(", ".join)
    st.dataframe(frame.set_index("provider"), width="stretch")

st.divider()

# --- Refresh --------------------------------------------------------------

st.subheader("Refresh")
st.caption(
    "Prices and macro are cheap (Tiingo + FRED). Profiles and fundamentals spend "
    "FMP's 250/day budget — roughly 6 calls per equity."
)

what = st.multiselect(
    "What to refresh", ["prices", "macro", "profile", "fundamentals"],
    default=["prices", "macro"],
)
left, right = st.columns(2)
period_type = left.radio("Fundamentals period", ["annual", "quarterly"], horizontal=True)
full_history = right.toggle(
    "Full history (backfill)", value=False,
    help="Off = only the last 10 days of bars, which is what a daily run needs.",
)

if st.button("Run refresh", type="primary", disabled=not what):
    with st.spinner("Refreshing… this can take a minute or two."):
        report, error = api.post(
            "/refresh",
            params={"what": what, "period_type": period_type, "full_history": full_history},
        )
    if error:
        st.error(error)
    else:
        st.success(
            f"{report['ok']} ok, {report['failed']} failed · "
            f"{report['rows_stored']:,} rows in {report['seconds']}s · "
            f"via {', '.join(report['providers_used'])}"
        )
        for failure in report["errors"]:
            st.warning(f"{failure['capability']} {failure['target']}: {failure['error'][:300]}")
        api.cached_get.clear()

st.divider()

# --- Single fetch ---------------------------------------------------------

st.subheader("Fetch one")
c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
capability = c1.selectbox("Capability", ["prices", "fundamentals", "profile", "macro"])
target = c2.text_input("Ticker / series id", "AAPL").strip().upper()
pin = c3.selectbox(
    "Provider", ["(use fallback chain)", "tiingo", "fmp", "fiscal_ai", "sec_edgar",
                 "twelve_data", "alpaca", "yfinance", "fred"],
)
if c4.button("Fetch", disabled=not target):
    params = {} if pin.startswith("(") else {"only": pin}
    with st.spinner(f"Fetching {target}…"):
        result, error = api.post(f"/ingest/{capability}/{target}", params=params)
    if error:
        st.error(error)
    else:
        st.success(f"{result['target']}: {result['rows_stored']:,} rows via **{result['provider']}**")
        st.json(result)
        api.cached_get.clear()

st.divider()

# --- Watchlist ------------------------------------------------------------

st.subheader("Watchlist")
watchlist, wl_error = api.get("/watchlist")
if wl_error:
    st.warning(wl_error)
else:
    cost = watchlist["estimated_calls"]
    st.caption(
        f"{cost.get('equities', 0)} US equities · {cost.get('mexico', 0)} Mexican (BMV) · "
        f"{cost.get('etfs', 0)} ETFs · {cost.get('macro', 0)} macro series"
    )
    budget = st.columns(3)
    budget[0].metric("Priced symbols", cost.get("priced_symbols", 0))
    budget[1].metric("Metered price calls", cost.get("metered_price_calls", 0),
                     help="BMV names route straight to yfinance, which has no daily cap.")
    budget[2].metric("Full fundamentals pass", f"{cost.get('fundamentals_calls', 0)} FMP calls",
                     help="Against a 250/day free tier, so runs rotate over several days.")
    if cost.get("fundamentals_calls", 0) > 210:
        st.info(
            f"A full fundamentals pass costs {cost['fundamentals_calls']} FMP calls "
            "against a 250/day tier, so refreshes rotate: each run takes the "
            "most out-of-date slice that fits the remaining budget, and the "
            "watchlist comes fully current over a few days."
        )
    for kind in ("equities", "mexico", "etfs", "macro"):
        current = watchlist.get(kind, [])
        st.write(f"**{kind}** ({len(current)})")
        with st.expander("show", expanded=len(current) <= 30):
            st.code(", ".join(current) or "—", language=None)
        add_col, remove_col = st.columns(2)
        added = add_col.text_input(f"Add to {kind}", "", key=f"add_{kind}",
                                   placeholder="comma-separated")
        if add_col.button(f"Add", key=f"addbtn_{kind}", disabled=not added.strip()):
            items = [i.strip().upper() for i in added.split(",") if i.strip()]
            _, error = api.post(f"/watchlist/{kind}", json={"items": items})
            st.error(error) if error else st.rerun()
        removed = remove_col.multiselect(f"Remove from {kind}", current, key=f"rm_{kind}")
        if remove_col.button("Remove", key=f"rmbtn_{kind}", disabled=not removed):
            import httpx
            httpx.request("DELETE", f"{api.BASE_URL}/watchlist/{kind}",
                          params={"items": removed}, headers=api._headers(), timeout=30)
            st.rerun()

st.divider()

# --- Log ------------------------------------------------------------------

st.subheader("Fetch log")
st.caption("Every provider attempt, including skips and why. This is how you tell "
           "which source a stored number came from.")
logs, logs_error = api.get("/logs", {"limit": 200})
if logs_error:
    st.warning(logs_error)
elif logs["entries"]:
    frame = pd.DataFrame(logs["entries"])
    only_problems = st.toggle("Only skips and failures", value=False)
    if only_problems:
        frame = frame[frame["status"] != "ok"]
    st.dataframe(frame, width="stretch", height=420, hide_index=True)
else:
    st.caption("No fetches recorded yet.")
