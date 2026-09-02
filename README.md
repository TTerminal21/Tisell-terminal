# Tisell Terminal

Personal financial research terminal. See `../MD Instruction/claude-code-project-brief.md`
for the full plan.

**Status: v1 items 1, 2, 3, 4, 6, 7, 8 and 9 complete.** A multi-source data layer with
provider fallback, quota accounting and a scheduled refresh, writing to DuckDB
and served over FastAPI behind a shared API-key header; a nine-view Streamlit
dashboard with top navigation and a multi-asset workspace; an analytics layer
covering option pricing, DCF, portfolio optimisation and risk; and a backup
script, and CNBV ingestion for Mexican issuers. The `textual` TUI (item 5)
was dropped from scope; everything else in the v1 brief is built.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env                        # then fill in your API keys
```

Requires Python 3.11+.

## Run

Double-click **`Tisell Terminal.command`** (macOS) or **`Tisell Terminal.bat`**
(Windows). Both start the data layer and the dashboard together, wait for the
API, open the browser, and shut down on Ctrl-C. Keep the console window open —
closing it stops the terminal.

Or from a shell:

```bash
python run.py
```

Moving the project to the Windows machine: see `SETUP-WINDOWS.md`. The short
version is copy everything except `.venv`, install Python 3.11+, and rebuild
the environment from `requirements.txt`.

Starts the data layer and the dashboard together, waits for the API, opens the
browser, and shuts both down on Ctrl-C. Dashboard on
<http://localhost:8501>, API docs on <http://localhost:8000/docs>.

To run them separately:

```bash
.venv/bin/uvicorn --app-dir data-layer main:app --reload --port 8000
.venv/bin/streamlit run dashboard/app.py
```

## Dashboard

Navigation sits across the top, grouped Research / Markets / Analytics /
System. Views share one **open-asset workspace**: a name opened anywhere stays
open everywhere, up to 8 at a time, shown as a row of pills with a close button.

| Page | What's there |
| --- | --- |
| **Overview** | Search any asset, see its general info: profile, key stats, price, fundamentals snapshot |
| **Watchlist** | Macro strip, returns across 1D–1Y with a heatmap, vol, max drawdown, RSI |
| **Charts** | Candlesticks + volume, SMA/EMA, Bollinger, RSI and MACD panels, 1M–ALL |
| *(search)* | Any page's sidebar: search a company by name, then fetch it inline |
| **Fundamentals** | Valuation / profitability / liquidity / leverage cards, full statements, Excel export |
| **Macro** | Multi-series FRED charts with optional rebasing, latest table |
| **Screener** | Watchlist-wide filter and sort on returns, risk and ratios |
| **Pricing** | Black-Scholes + Greeks, Monte Carlo cross-check, implied vol, two-stage DCF with sensitivity grid |
| **Portfolio** | Max-Sharpe / min-variance / risk-parity optimisation, risk metrics, correlation heatmap, VaR/CVaR, stress tests, efficient frontier, Excel report |
| **Data** | Quota bars, provider chains, refresh, single fetch, watchlist editing, fetch log |

Reads never call a provider, so browsing costs no quota. Only the **Data**
page spends it.

## Auth

The data layer sits behind an `X-API-Key` header. Set `DATA_LAYER_API_KEY` in
`.env` — the dashboard reads the same variable, so both ends stay in sync.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Leaving it blank disables the check, which is a reasonable choice on a single
machine. `/health` and `/docs` stay open either way, so a client can tell
"backend is down" from "my key is wrong".

## Backups

```bash
.venv/bin/python data-layer/backup.py --keep 6
```

Checkpoints the WAL through a real connection before copying, so the copy is
consistent. Destination defaults to `./backups`, or set `BACKUP_DIR`.

## Refreshing data

```bash
.venv/bin/python data-layer/refresh.py                 # daily: prices + macro
.venv/bin/python data-layer/refresh.py --full          # weekly: + profiles + fundamentals
.venv/bin/python data-layer/refresh.py --full --full-history   # first-time backfill
.venv/bin/python data-layer/refresh.py --what fundamentals --limit 20
```

### Rotation

The watchlist is larger than any single day's free-tier budget: a full
fundamentals pass over the US names costs roughly 995 FMP calls against a
250/day tier. Runs therefore **rotate** — each one takes the most out-of-date
slice that fits the remaining budget (never-fetched names first, then oldest),
and the whole watchlist comes current over a few days. A reserve is held back
so a scheduled run never consumes the entire budget and lock you out of ad-hoc
fetches in the dashboard.

Prices are not subject to this: US names cost one Tiingo call each against a
500/day tier, and BMV names go to yfinance, which is unmetered.

Point Task Scheduler (Windows) or cron at the first line for a daily run. A
daily run costs no FMP calls at all — prices come from Tiingo, macro from FRED.
A `--full` run costs roughly 6 FMP calls per equity, which is what keeps the
watchlist small.

Single items, on demand:

```bash
.venv/bin/python data-layer/ingest.py prices AAPL --start 2020-01-01
.venv/bin/python data-layer/ingest.py fundamentals AAPL --period quarterly
.venv/bin/python data-layer/ingest.py macro DGS10
```

## Sources and fallback

Each data need has an ordered chain in `config.PROVIDER_ORDER`. The first
provider that is configured, within its daily budget, and able to answer wins.
Unconfigured and exhausted providers are skipped without spending a call.

| Need | Chain |
| --- | --- |
| prices | tiingo → fmp → twelve_data → alpaca → yfinance |
| fundamentals | cnbv → fmp → fiscal_ai → sec_edgar → yfinance |
| profile | fmp → fiscal_ai → yfinance |
| macro | fred |

Every attempt — including each skip and why — is written to `fetch_log`, so any
stored number can be traced to the provider that supplied it (`GET /logs`).

### Non-US listings

Tiingo and FMP carry **no BMV coverage**, and FMP's free tier answers HTTP 402
for non-US listings — including the NYSE-listed ADRs (CX, AMX). Fiscal.ai
cannot resolve BMV symbols either. Mexican fundamentals therefore come from
**CNBV**, the regulator these companies actually file with (see below).

Both US-only providers declare this through a `supports()` check, so a `.MX`
symbol is skipped without spending a call rather than tried and failed. Routing
50 BMV names through the full chain would otherwise burn 50 Tiingo and 50 FMP
calls a day to learn nothing.

Mexican symbols carry an exchange suffix you would never guess — La Comer is
`LACOMERUBC.MX`, Liverpool is `LIVEPOLC-1.MX`, Peñoles is `PE&OLES.MX` — so use
the search box rather than typing tickers.

### CNBV — Mexican as-filed financials

`sources/cnbv.py` reads the CNBV XBRL portal, which is the as-filed ground
truth for BMV issuers and the only source with any BMV fundamentals coverage.

There is no documented API. The portal is an AngularJS app; its backend was
worked out by watching what the page calls:

| Endpoint | Notes |
| --- | --- |
| `POST /DocumentoInstancia/ConsultarEnviosInformacionDataTable` | DataTables protocol plus a `clavePizarra` filter. The standard `search[value]` parameter is **not** supported — a non-empty one makes it return a bare `null`. |
| `POST /DocumentoInstancia/BajarArchivoDocumentoInstancia` | `{idDocIns, tipoArchivo: 4}` returns the filing as pre-parsed JSON (gzipped, ~11 MB). The XBRL is already resolved into facts, contexts and units, so no XBRL parser is needed. |

Neither endpoint needs a key, cookie or session.

Two things the implementation has to get right:

- **Symbol mapping.** Yahoo appends the share series (`LACOMERUBC.MX`), CNBV
  files under the bare clave (`LACOMER`). No rule recovers one from the other,
  so candidates are generated by stripping known series suffixes and confirmed
  against the API, then cached.
- **Dimensional facts are skipped.** They are segment and equity-component
  breakdowns; including them alongside the headline figures double-counts every
  total. Only non-dimensional contexts are stored.
- **Presentation order and statement names** come from the filing's
  `RolesPresentacion` tree, so a CNBV income statement renders Revenue → Cost of
  Sales → Gross Profit exactly as filed, split across the same statement tabs
  the US sources use.

Filings are quarterly; Q4 carries the full-year cumulative figures, so that is
what an `annual` request returns. Currency is per-issuer — CEMEX reports in USD,
Walmex in MXN — and is read from the filing's own units.

**TLS note:** `xbrl.cnbv.gob.mx` serves its leaf certificate without the
GlobalSign intermediate that signs it, so OpenSSL cannot build a chain. curl and
browsers hide this by chasing the AIA extension; Python does not. The fix in
`sources/cnbv.py` is to ship that public intermediate in `data-layer/certs/` and
add it to certifi's roots — **verification stays fully enabled**. It expires
2028-11-21; refresh it from the leaf's AIA URL if it ever starts failing.

Provider notes worth knowing:

- **FMP** free tier rejects `limit > 5` with HTTP 402, and its `/api/v3`
  endpoints are gone for accounts created after 31 Aug 2025. This project uses
  `stable` and clamps the limit.
- **SEC EDGAR** needs no key but does need `SEC_EDGAR_USER_AGENT`; blank means
  it is skipped rather than 403ing.
- **Tiingo** free tier throttles at **50 requests/hour** as well as 500/day,
  and the hourly cap is what binds during a bulk backfill — it answered 429 on
  173 of 270 requests in one run while the daily counter still showed budget.
  `quota.py` models both, and `has_budget()` refuses when either is spent.
- **Twelve Data** and **Alpaca** are implemented but untested — no keys were
  configured when they were written.
- **ETFs** live under `etfs` in the watchlist and are refreshed for prices and
  profile only. They have no income statement, so fetching fundamentals for one
  just burns four provider calls to rediscover that.

## Endpoints

| Method | Path | Does |
| --- | --- | --- |
| GET | `/health` | Row counts per table |
| GET | `/providers` | Which providers are configured, and the chains |
| GET | `/quota` | Calls used today per provider vs. free-tier limits |
| GET | `/logs` | Recent fetch attempts, including skips and failures |
| GET | `/watchlist` | Current watchlist + estimated refresh cost |
| PUT/POST/DELETE | `/watchlist[/{kind}]` | Replace, add to, remove from |
| GET | `/search` | Resolve a company name to tickers (`?q=La Comer`) |
| GET | `/tickers` | Stored tickers with coverage |
| GET | `/prices/{ticker}` | Stored daily bars; `?start=&end=` |
| GET | `/fundamentals/{ticker}` | `?period_type=&statement=&metric=` |
| GET | `/profile/{ticker}` | Company profile |
| GET | `/macro` · `/macro/{series_id}` | Macro series and observations |
| POST | `/ingest/{capability}/{target}` | Fetch via the chain; `?only=` pins one provider |
| POST | `/refresh` | Refresh the whole watchlist |

Reads and writes are deliberately separate: read endpoints serve DuckDB and
never call a provider, so only `/ingest` and `/refresh` spend quota.

## Analytics

`analytics/` is a plain Python package at the repo root — no network, no
DuckDB — so any frontend can import it and it is testable
without a running data layer.

| Module | Contains |
| --- | --- |
| `pricing.py` | Black-Scholes with Greeks, implied vol (Brent), antithetic Monte Carlo, GBM paths |
| `dcf.py` | Two-stage DCF, WACC/CAPM helpers, sensitivity grid |
| `risk.py` | Returns, covariance/correlation, Sharpe/Sortino/beta/alpha, mean-variance and risk-parity optimisation, historical and bootstrap VaR/CVaR, scenario stress tests, efficient frontier |

Everything runs on **adjusted** closes. This is not optional: a split in a raw
close reads as a ~50% one-day loss and would wreck every covariance, beta and
drawdown number.

Verified against references — Black-Scholes reproduces the textbook
S=100/K=100/T=1/r=5%/σ=20% case to four decimals (call 10.4506, put 5.5735),
put-call parity holds to 1e-10, Monte Carlo brackets the closed form inside its
95% interval, and risk parity equalises risk contributions to 14.29% across
seven assets.

## Appearance

The chrome is deliberately quiet so the data carries the colour: green and red
mean "up" and "down" and are never used for buttons, headers or borders, so a
red number always means a red number.

- `.streamlit/config.toml` sets only the accent and the default mode. Background
  and text are left to Streamlit on purpose — hardcoding them silently
  overrides `base`, which makes the chrome and the custom stylesheet disagree
  (chrome light, tokens dark). That bug is why the file is this short.
- `dashboard/theme.py` holds the palette tokens and the stylesheet.
  `theme.palette()` is the single source of truth, and `api.theme()` delegates
  to it so charts can never diverge from the page.
- `dashboard/ui.py` holds shared components and, more importantly, the number
  formatters — a terminal that prints `4593416000` in one place and `4.59B` in
  another is harder to read than one that is merely plain.
- `dashboard/labels.py` turns raw taxonomy ids into readable names
  (`ifrs-full_ProfitLossFromOperatingActivities` → "Operating profit"). A
  curated table covers the lines people read; a camel-case fallback keeps the
  long tail in English rather than falling back to the raw id.

**Default is dark.** Change `base` in `.streamlit/config.toml` to `"light"`, or
switch it per-session in Streamlit's own settings menu — both palettes are
implemented and tested.

Selectors target documented `data-testid` attributes only. If Streamlit changes
one, the page still renders and just loses that flourish; nothing structural is
expressed in CSS.

## Layout

```
data-layer/
  config.py        # .env loading, provider order per capability
  db.py            # DuckDB schema + idempotent upsert
  quota.py         # per-provider daily call accounting
  registry.py      # the fallback chain
  watchlist.py     # watchlist load/save/cost estimate
  ingest.py        # fetch -> store, one path per capability, plus a CLI
  refresh.py       # batch refresh across the watchlist, plus a CLI
  main.py          # FastAPI app
  sources/         # one module per provider, one shared contract
    _http.py       #   quota-counted GET with uniform error mapping
    tiingo.py  fmp.py  fred.py  fiscal_ai.py  sec_edgar.py
    twelve_data.py  alpaca.py  yfinance_source.py
  watchlist.json   # the watchlist itself
  terminal.duckdb  # created on first run, git-ignored
dashboard/
  app.py           # Streamlit; talks only to the API
```

## Data model notes

Fundamentals are stored **long** — one row per
`(ticker, period_end, period_type, statement, metric)` — because providers name
and cover line items differently.

Each row also carries an **`ordinal`**: the line's position within its statement
as the filer presented it. Without it a pivot sorts alphabetically, which turns
an income statement into "AdministrativeExpense, CostOfSales, FinanceCosts…" —
meaningless for a statement that reads top to bottom. CNBV supplies real order
from the filing's own presentation hierarchy (which also yields the true
statement split: income, balance, cash flow, comprehensive income, changes in
equity); FMP, Fiscal.ai and yfinance supply it from their response field order.
Rows without an ordinal sort last, alphabetically. A wide table would need migrating every time
a provider is added.

A consequence worth knowing: two providers can both hold a figure for the same
period under their own metric names (FMP's `revenue` and Fiscal.ai's
`income_statement_total_revenues`), so they coexist rather than overwrite. That
makes cross-source reconciliation possible, but means a naive `SELECT *` can
double-count. Filter on `source` when that matters.

Charts draw raw OHLC. The adjusted series (`adj_open`/`adj_high`/`adj_low`/
`adj_close`) is stored alongside it but not yet plotted, so a chart spanning a
split shows the unadjusted jump. Returns-based analytics should read the
adjusted columns.

Quota days roll over at **UTC midnight**, which is when most of these providers
reset.

## Watchlist

Four groups, because they need different treatment rather than for tidiness:

| Group | Count | Gets |
| --- | --- | --- |
| `equities` | 199 US listings | prices, profile, fundamentals |
| `mexico` | 63 BMV listings | prices, profile, **as-filed fundamentals via CNBV** |
| `etfs` | 32 | prices, profile (no income statement) |
| `macro` | 23 FRED series | observations |

Edit `data-layer/watchlist.json` or use the **Data** page. Every symbol in it
was verified to actually return data before being added; names that were
delisted or renamed (Elektra, Alfa, Crédito Real, Lala, IEnova) are excluded.

**Correction on Pemex:** it has no listed *equity*, so it cannot be priced —
but it does file with the CNBV as a debt issuer, and its filings are reachable
through `sources/cnbv.py` by clave `PEMEX`. It is not in the watchlist because
the watchlist is priced instruments.

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

67 tests over the analytics layer and the data layer's pure functions. Every
case corresponds to a bug that actually shipped and was found by reading output
by hand — risk parity converging to the wrong fixed point, NaN reaching DuckDB
and breaking JSON serialisation, FMP's limit cap silently demoting it to a
fallback, US-only providers burning calls on BMV symbols. Two of those were
completely silent; an optimiser returning confident nonsense does not announce
itself.

They need no network and no running data layer.

## Not yet built

Nothing from the v1 brief. The `textual` TUI (item 5) was dropped from scope
by choice, not left undone.
GPU (CuPy) Monte Carlo paths are not wired up — the CPU path is fast enough at
current sizes, and this machine has no CUDA anyway.


