# -*- coding: utf-8 -*-
"""Спільне для сторінок Streamlit-застосунку walmartPayments.

`streamlit run` виконує файл сторінки як окремий скрипт, а не як частину
пакета — відносні імпорти (`from ..config import ...`) впадуть з
ImportError. Тому кожна сторінка спершу імпортує цей модуль, який кладе
корінь проєкту (батько `webapp/` — містить `config.py`, `api.py`, `cogs/`
напряму, БЕЗ обгортки `walmartPayments/`: саме так лежить деплой на
Streamlit Cloud, репозиторій = цей каталог) у sys.path і лише тоді
імпортує решту модулів як top-level (`import config`, `from cogs import
store`, ...) — без крапки, без префіксу `walmartPayments.`."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import os  # noqa: E402

import streamlit as st  # noqa: E402

# На Streamlit Cloud немає .env — креди задаються через secrets.toml в UI.
# Переносимо їх у os.environ ДО імпорту config (config читає env при імпорті).
# Локально, без secrets.toml, st.secrets кидає виняток — просто ігноруємо.
try:
    for _key, _value in st.secrets.items():
        if isinstance(_value, str):
            os.environ.setdefault(_key, _value)
except Exception:
    pass

import config  # noqa: E402

# Service account JSON не лежить в репо (credentials/ в .gitignore) — якщо
# він є в secrets.toml (блок [gcp_service_account]), пишемо/перезаписуємо
# його у фіксований шлях при КОЖНОМУ старті процесу. Без "if not exists":
# на ефемерному диску контейнера файл, записаний старим ключем, інакше
# ніколи не оновиться після зміни secrets — тільки disk-wipe при redeploy.
try:
    _sa = st.secrets.get("gcp_service_account")
except Exception:
    _sa = None
if _sa:
    import json as _json
    config.GOOGLE_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_CREDENTIALS_FILE.write_text(_json.dumps(dict(_sa)))
from api import WalmartAPIError, WalmartPaymentsAPI  # noqa: E402
from cogs import store as cogs_store  # noqa: E402
from cogs.ad_spend import AdSpendImportError, total_ad_spend  # noqa: E402
from cogs.calculator import compute_cogs  # noqa: E402
from cogs.price_import import (  # noqa: E402
    PriceImportError, import_from_xlsx,
)
from cogs.store import SENTINEL_DATE  # noqa: E402
from main import PAYOUT_DATE_FORMAT, resolve_payout_date  # noqa: E402
from parser import ReconParseError, parse_recon_zip  # noqa: E402
from sheets_report import (  # noqa: E402
    SheetsExportError, export_to_sheets,
)
from summary import (  # noqa: E402
    build_item_breakdown, build_statement, build_summary,
)

__all__ = [
    "config", "WalmartAPIError", "WalmartPaymentsAPI", "cogs_store",
    "AdSpendImportError", "total_ad_spend",
    "compute_cogs", "import_from_xlsx", "PriceImportError", "SENTINEL_DATE",
    "PAYOUT_DATE_FORMAT", "resolve_payout_date", "ReconParseError",
    "parse_recon_zip",
    "SheetsExportError", "export_to_sheets",
    "build_item_breakdown", "build_statement", "build_summary",
    "page_header", "get_db_connection", "get_api_client",
    "reload_config", "format_money", "payout_date_to_date",
]


def _require_auth() -> None:
    """Проста password-gate для деплою: якщо в secrets.toml задано
    APP_PASSWORD — застосунок недоступний без нього. Якщо не задано (звичний
    локальний запуск) — гейт нічого не робить."""
    try:
        app_password = st.secrets.get("APP_PASSWORD")
    except Exception:
        app_password = None
    if not app_password:
        return
    if st.session_state.get("_authed"):
        return
    st.title("🔒 Вхід")
    pwd = st.text_input("Пароль", type="password")
    if st.button("Увійти", type="primary"):
        if pwd == app_password:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Невірний пароль.")
    st.stop()


def page_header(title: str, icon: str = "📦") -> None:
    st.set_page_config(page_title=f"{title} — Walmart Payments", page_icon=icon,
                       layout="wide")
    _require_auth()
    st.title(f"{icon} {title}")


@st.cache_resource
def get_db_connection():
    """Одне з'єднання з COGS-базою на весь процес Streamlit."""
    return cogs_store.connect(config.COGS_DB_PATH)


@st.cache_resource
def get_api_client() -> WalmartPaymentsAPI:
    """Кешований клієнт: тротлінг (45с між запитами) живе в інстансі,
    тому пересоздавати його на кожен st rerun не можна — інакше пауза
    між запитами Walmart API тихо зникає.

    Креди передаються явно (а не через дефолти __init__) — дефолти
    WalmartPaymentsAPI.__init__ обчислюються один раз при імпорті api.py
    і не бачать значень, підвантажених пізніше через reload_config()."""
    return WalmartPaymentsAPI(
        client_id=config.CLIENT_ID,
        client_secret=config.CLIENT_SECRET,
        seller_id=config.SELLER_ID,
    )


def reload_config() -> None:
    """Після збереження .env на сторінці Креди — перечитати .env (з
    override=True, інакше другий поспіль save мовчки не подіє: старі
    значення вже сидять у os.environ) і скинути кеш клієнта."""
    import importlib

    from dotenv import load_dotenv

    load_dotenv(config.ENV_FILE, override=True)
    importlib.reload(config)
    get_api_client.clear()


def format_money(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} {currency}"


def payout_date_to_date(payout_date: str) -> date:
    return datetime.strptime(payout_date, PAYOUT_DATE_FORMAT).date()
