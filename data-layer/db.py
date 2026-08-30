"""DuckDB storage for the data layer.

DuckDB takes an exclusive lock on the file for as long as a connection is open,
so every operation opens and closes its own connection rather than holding one
for the process lifetime. That keeps the API server and the command-line fetch
scripts from locking each other out. A module-level lock serialises writes from
FastAPI's worker threads within a single process.

Fundamentals are stored long rather than wide - one row per (ticker, period,
metric) - because each provider names and covers line items differently. A wide
table would need migrating every time a provider is added; this one does not.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Iterator

import duckdb

from config import DUCKDB_PATH

_write_lock = threading.Lock()

SCHEMA_STATEMENTS = [
    # --- Prices -----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS prices (
        ticker        VARCHAR   NOT NULL,
        date          DATE      NOT NULL,
        open          DOUBLE,
        high          DOUBLE,
        low           DOUBLE,
        close         DOUBLE,
        volume        BIGINT,
        adj_open      DOUBLE,
        adj_high      DOUBLE,
        adj_low       DOUBLE,
        adj_close     DOUBLE,
        adj_volume    BIGINT,
        div_cash      DOUBLE,
        split_factor  DOUBLE,
        source        VARCHAR   NOT NULL,
        fetched_at    TIMESTAMP NOT NULL,
        PRIMARY KEY (ticker, date)
    );
    """,
    # --- Fundamentals -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS fundamentals (
        ticker       VARCHAR   NOT NULL,
        period_end   DATE      NOT NULL,
        period_type  VARCHAR   NOT NULL,  -- annual | quarterly | ttm
        statement    VARCHAR   NOT NULL,  -- income | balance | cash_flow | ratios | metrics
        metric       VARCHAR   NOT NULL,
        value        DOUBLE,
        currency     VARCHAR,
        -- Position of this line within its statement, as the filer presented
        -- it. Without this a statement renders alphabetically, which is
        -- meaningless for a P&L that reads top-to-bottom.
        ordinal      INTEGER,
        source       VARCHAR   NOT NULL,
        fetched_at   TIMESTAMP NOT NULL,
        PRIMARY KEY (ticker, period_end, period_type, statement, metric)
    );
    """,
    # Migration for stores created before `ordinal` existed.
    "ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS ordinal INTEGER;",
    # --- Company profile --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS profiles (
        ticker      VARCHAR   NOT NULL,
        name        VARCHAR,
        exchange    VARCHAR,
        currency    VARCHAR,
        sector      VARCHAR,
        industry    VARCHAR,
        country     VARCHAR,
        cik         VARCHAR,
        market_cap  DOUBLE,
        beta        DOUBLE,
        description VARCHAR,
        source      VARCHAR   NOT NULL,
        fetched_at  TIMESTAMP NOT NULL,
        PRIMARY KEY (ticker)
    );
    """,
    # --- Macro ------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS macro_series (
        series_id   VARCHAR   NOT NULL,
        title       VARCHAR,
        units       VARCHAR,
        frequency   VARCHAR,
        seasonal    VARCHAR,
        source      VARCHAR   NOT NULL,
        fetched_at  TIMESTAMP NOT NULL,
        PRIMARY KEY (series_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_observations (
        series_id   VARCHAR   NOT NULL,
        date        DATE      NOT NULL,
        value       DOUBLE,
        source      VARCHAR   NOT NULL,
        fetched_at  TIMESTAMP NOT NULL,
        PRIMARY KEY (series_id, date)
    );
    """,
    # --- Operational ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS quota_usage (
        provider    VARCHAR NOT NULL,
        usage_date  DATE    NOT NULL,
        calls       BIGINT  NOT NULL DEFAULT 0,
        PRIMARY KEY (provider, usage_date)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_log (
        run_at      TIMESTAMP NOT NULL,
        capability  VARCHAR   NOT NULL,
        target      VARCHAR   NOT NULL,
        provider    VARCHAR   NOT NULL,
        status      VARCHAR   NOT NULL,  -- ok | failed | skipped
        rows        BIGINT,
        message     VARCHAR
    );
    """,
]


@contextlib.contextmanager
def connect() -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a connection with the schema applied, and close it on the way out."""
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        for statement in SCHEMA_STATEMENTS:
            con.execute(statement)
        yield con
    finally:
        con.close()


def write_lock() -> threading.Lock:
    return _write_lock


def upsert(con: duckdb.DuckDBPyConnection, table: str, key_columns: list[str],
           columns: list[str], rows: list[tuple]) -> int:
    """Delete-then-insert on the key columns.

    DuckDB's ON CONFLICT support varies by version; this shape behaves the same
    everywhere and keeps a re-fetch idempotent.
    """
    if not rows:
        return 0
    key_index = [columns.index(name) for name in key_columns]

    # Collapse duplicate keys within the incoming batch. Without this a
    # provider that returns the same fact twice (restatements, equivalent XBRL
    # contexts) fails the whole transaction on a primary-key violation. First
    # occurrence wins, so callers control precedence by ordering their rows.
    deduped: dict[tuple, tuple] = {}
    for row in rows:
        deduped.setdefault(tuple(row[i] for i in key_index), row)
    rows = list(deduped.values())

    keys = [tuple(row[i] for i in key_index) for row in rows]
    where = " AND ".join(f"{name} = ?" for name in key_columns)
    placeholders = ", ".join("?" for _ in columns)

    con.execute("BEGIN TRANSACTION")
    try:
        con.executemany(f"DELETE FROM {table} WHERE {where}", keys)
        con.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)
