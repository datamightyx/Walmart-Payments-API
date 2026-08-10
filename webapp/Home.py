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

st.caption(
    "Розрахунок COGS для Walmart-виплат — версійовані ціни (зміна ціни не "
    "чіпає вже пораховані періоди) і незмінна історія запусків."
)

conn = get_db_connection()
sku_count = len(cogs_store.all_skus(conn))
runs = cogs_store.list_runs(conn, limit=1)
last_run = runs[0] if runs else None

with st.container(border=True):
    col1, col2, col3 = st.columns(3)
    col1.metric("SKU з ціною", sku_count)
    col2.metric("Запусків COGS", len(cogs_store.list_runs(conn)))
    col3.metric("Останній запуск", last_run["run_at"] if last_run else "—")

if not config.CLIENT_ID or not config.CLIENT_SECRET:
    st.warning(
        "Walmart API credentials не задані — застосунок не зможе тягнути "
        "звіти. Задай на сторінці **🔑 Credentials**.",
        icon="⚠️",
    )

st.subheader("Навігація", anchor=False)

nav_items = [
    ("pages/1_🔑_Credentials.py", "Credentials", "🔑",
     "Walmart API / Google Sheets / R2"),
    ("pages/2_💲_Ціни.py", "Ціни", "💲",
     "Імпорт прайсу, ручне редагування, історія цін по SKU"),
    ("pages/3_▶️_Запуск.py", "Запуск", "▶️",
     "Обрати звіт і дату, порахувати COGS, вивантажити у Sheets"),
    ("pages/4_📜_Історія.py", "Історія", "📜",
     "Усі попередні запуски розрахунку COGS (незмінні)"),
]
nav_cols = st.columns(4)
for col, (path, label, icon, help_text) in zip(nav_cols, nav_items):
    with col:
        with st.container(border=True):
            st.page_link(path, label=f"**{label}**", icon=icon)
            st.caption(help_text)
