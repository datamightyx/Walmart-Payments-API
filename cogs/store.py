# -*- coding: utf-8 -*-
"""SQLite-сховище для цін (версійованих) і історії запусків COGS.

Ключ товару — `sku`, це те саме, що `Partner Item Id` у recon CSV (перевірено
емпірично: значення в CSV співпадають з колонкою SKU у прайс-файлі, а не з
числовим Walmart Item ID).

Ціни версіоновані по `effective_from`: для дати X діє той запис, у якого
`effective_from` найбільший серед тих, що <= X. Це і дає незмінність
минулих розрахунків при зміні ціни — треба лише завжди питати ціну "станом
на дату періоду", а не "поточну".

`cogs_runs` / `cogs_run_items` — append-only історія запусків. Рядки ніколи
не оновлюються і не видаляються; повторний розрахунок того самого періоду
створює новий run_id зі своїм знімком цін.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

SENTINEL_DATE = date(1900, 1, 1)  # "діяла завжди" — для першого імпорту SKU

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    item_id TEXT,
    asin TEXT,
    product_name TEXT,
    unit_cost REAL NOT NULL,
    effective_from TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(sku, effective_from)
);
CREATE INDEX IF NOT EXISTS idx_prices_sku ON prices(sku, effective_from);

CREATE TABLE IF NOT EXISTS cogs_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    payout_date TEXT,
    source_file TEXT,
    as_of_date TEXT NOT NULL,
    total_units INTEGER NOT NULL,
    total_revenue REAL NOT NULL,
    total_cogs REAL NOT NULL,
    missing_price_skus TEXT NOT NULL DEFAULT '',
    triggered_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cogs_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES cogs_runs(run_id),
    sku TEXT NOT NULL,
    product_name TEXT,
    units INTEGER NOT NULL,
    unit_cost REAL,
    total_cost REAL NOT NULL,
    revenue REAL NOT NULL,
    price_effective_from TEXT,
    price_missing INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_items_run ON cogs_run_items(run_id);

-- Ручні дані на весь розрахунковий період (не версіоновані, як ціни —
-- просто останнє введене значення; на відміну від cogs_runs, це не
-- знімок історії, а редагований раз-на-період вхід типу рекламних витрат).
CREATE TABLE IF NOT EXISTS period_extras (
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    ad_spend REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (period_start, period_end)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Відкриває (створюючи за потреби) базу і схему.

    Якщо R2 налаштований (config.R2_*) — тягне свіжу копію з R2 поверх
    local_path ДО відкриття з'єднання, тож переживає redeploy на
    ефемерному хостингу (див. cogs/r2_sync.py)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from . import r2_sync
    if r2_sync.enabled():
        r2_sync.download_db(path)
    # check_same_thread=False: Streamlit кешує ресурси (st.cache_resource) і
    # може використовувати одне й те саме з'єднання з різних сесій/потоків.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _sync_to_r2(conn: sqlite3.Connection) -> None:
    """Викликається одразу після кожного commit() — заливає файл бази в R2,
    якщо він налаштований. No-op інакше."""
    from . import r2_sync
    if not r2_sync.enabled():
        return
    row = conn.execute("PRAGMA database_list").fetchone()
    if row and row["file"]:
        r2_sync.upload_db(Path(row["file"]))


# ── ціни ──────────────────────────────────────────────────────────────────

def has_any_price(conn: sqlite3.Connection, sku: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM prices WHERE sku = ? LIMIT 1", (sku,)
    ).fetchone() is not None


def latest_price(
    conn: sqlite3.Connection, sku: str, as_of: date
) -> sqlite3.Row | None:
    """Ціна, що діяла станом на as_of (найновіша версія з effective_from <= as_of)."""
    return conn.execute(
        "SELECT * FROM prices WHERE sku = ? AND effective_from <= ? "
        "ORDER BY effective_from DESC, id DESC LIMIT 1",
        (sku, as_of.isoformat()),
    ).fetchone()


def insert_price_version(
    conn: sqlite3.Connection,
    *,
    sku: str,
    item_id: str | None,
    asin: str | None,
    product_name: str,
    unit_cost: float,
    effective_from: date,
    source: str,
) -> int:
    """Додає нову версію ціни. Той самий (sku, effective_from) замінює попередній
    запис на цю дату — не плодить дублікати при повторному імпорті того самого дня,
    але не чіпає версії на інших датах."""
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO prices (sku, item_id, asin, product_name, unit_cost, "
        "effective_from, created_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(sku, effective_from) DO UPDATE SET "
        "item_id=excluded.item_id, asin=excluded.asin, "
        "product_name=excluded.product_name, unit_cost=excluded.unit_cost, "
        "created_at=excluded.created_at, source=excluded.source",
        (sku, item_id, asin, product_name, unit_cost,
         effective_from.isoformat(), now, source),
    )
    conn.commit()
    _sync_to_r2(conn)
    return cur.lastrowid


def current_prices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Найновіша версія ціни для кожного sku (незалежно від дати — "сьогоднішня")."""
    return conn.execute(
        """
        SELECT p.* FROM prices p
        INNER JOIN (
            SELECT sku, MAX(effective_from) AS max_ef FROM prices GROUP BY sku
        ) latest ON p.sku = latest.sku AND p.effective_from = latest.max_ef
        ORDER BY p.sku
        """
    ).fetchall()


def price_history(conn: sqlite3.Connection, sku: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM prices WHERE sku = ? ORDER BY effective_from DESC, id DESC",
        (sku,),
    ).fetchall()


def all_skus(conn: sqlite3.Connection) -> list[str]:
    return [r["sku"] for r in conn.execute("SELECT DISTINCT sku FROM prices ORDER BY sku")]


# ── історія запусків ─────────────────────────────────────────────────────

def insert_run(conn: sqlite3.Connection, **fields) -> int:
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO cogs_runs ({cols}) VALUES ({placeholders})",
        tuple(fields.values()),
    )
    conn.commit()
    _sync_to_r2(conn)
    return cur.lastrowid


def insert_run_items(conn: sqlite3.Connection, run_id: int, items: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO cogs_run_items (run_id, sku, product_name, units, unit_cost, "
        "total_cost, revenue, price_effective_from, price_missing) "
        "VALUES (:run_id, :sku, :product_name, :units, :unit_cost, :total_cost, "
        ":revenue, :price_effective_from, :price_missing)",
        [{**item, "run_id": run_id} for item in items],
    )
    conn.commit()
    _sync_to_r2(conn)


def list_runs(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cogs_runs ORDER BY run_id DESC LIMIT ?", (limit,)
    ).fetchall()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM cogs_runs WHERE run_id = ?", (run_id,)
    ).fetchone()


def get_run_items(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cogs_run_items WHERE run_id = ? ORDER BY total_cost DESC",
        (run_id,),
    ).fetchall()


# ── ручні дані періоду (рекламні витрати тощо) ───────────────────────────

def get_period_extra(
    conn: sqlite3.Connection, period_start: date, period_end: date
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM period_extras WHERE period_start = ? AND period_end = ?",
        (period_start.isoformat(), period_end.isoformat()),
    ).fetchone()


def set_period_extra(
    conn: sqlite3.Connection, period_start: date, period_end: date, ad_spend: float
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO period_extras (period_start, period_end, ad_spend, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(period_start, period_end) DO UPDATE SET "
        "ad_spend=excluded.ad_spend, updated_at=excluded.updated_at",
        (period_start.isoformat(), period_end.isoformat(), ad_spend, now),
    )
    conn.commit()
    _sync_to_r2(conn)
