# -*- coding: utf-8 -*-
"""Walmart Payments — головна сторінка Streamlit-застосунку.

Запуск:
    streamlit run walmartPayments/webapp/Home.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import cogs_store, config, get_db_connection, page_header

import streamlit as st

page_header("Walmart Payments", "📦")

st.write(
    "Розрахунок COGS для Walmart-виплат: версійовані ціни (зміна ціни не "
    "чіпає вже пораховані періоди) + незмінна історія запусків."
)

conn = get_db_connection()
sku_count = len(cogs_store.all_skus(conn))
runs = cogs_store.list_runs(conn, limit=1)
last_run = runs[0] if runs else None

with st.container(border=True):
    col1, col2, col3 = st.columns(3)
    col1.metric("SKU з ціною", sku_count)
    col2.metric("Запусків COGS", len(cogs_store.list_runs(conn)))
    col3.metric(
        "Останній запуск",
        last_run["run_at"] if last_run else "—",
    )

st.divider()
st.subheader("Навігація")
nav1, nav2, nav3, nav4 = st.columns(4)
with nav1:
    st.page_link(
        "pages/1_🔑_Креди.py", label="Креди", icon="🔑",
        help="Walmart API / Google Sheets credentials",
    )
with nav2:
    st.page_link(
        "pages/2_💲_Ціни.py", label="Ціни", icon="💲",
        help="Імпорт прайсу, ручне редагування, історія цін по SKU",
    )
with nav3:
    st.page_link(
        "pages/3_▶️_Запуск.py", label="Запуск", icon="▶️",
        help="Обрати звіт і дату, порахувати COGS, вивантажити у Sheets",
    )
with nav4:
    st.page_link(
        "pages/4_📜_Історія.py", label="Історія", icon="📜",
        help="Усі попередні запуски розрахунку COGS (незмінні)",
    )

if not config.ENV_FILE.exists():
    st.info(
        "Креди зараз беруться з дефолтів у коді / змінних середовища — "
        "файл .env ще не створено. Збережи креди на сторінці **🔑 Креди**, "
        "щоб тримати їх поза кодом."
    )
