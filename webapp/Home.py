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

st.markdown(
    """
    <style>
    /* st.container(key=...) додає клас .st-key-<key> на саму картку —
    офіційний, стабільний спосіб (не вгадування internal testid). */

    .st-key-big-nav-card {
        position: relative; display: flex; flex-direction: column;
        min-height: 118px;
        background: linear-gradient(135deg, #0071dc, #004f9a) !important;
        border: none !important; border-radius: 16px !important;
        box-shadow: 0 4px 14px rgba(0, 113, 220, 0.35);
        transition: transform .12s ease, box-shadow .12s ease;
        overflow: hidden;
    }
    .st-key-big-nav-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(0, 113, 220, 0.45);
    }
    /* посилання розтягнуте на всю картку — клік працює будь-де в її межах */
    .st-key-big-nav-card [data-testid="stPageLink"] a {
        position: absolute; inset: 0; z-index: 2;
        display: flex; align-items: center; justify-content: center;
        padding-bottom: 26px;
    }
    .st-key-big-nav-card [data-testid="stPageLink"] a p {
        color: #ffffff !important; font-size: 1.45rem; font-weight: 700; margin: 0;
    }
    .st-key-big-nav-card .big-nav-caption {
        position: relative; z-index: 1;
        padding: 46px 16px 20px;
        text-align: center; color: #ffffff; opacity: 0.88; font-size: 0.92rem;
    }

    /* три менші картки — однакова висота, теж клікабельні цілком */
    [class^="st-key-small-nav-card"] {
        position: relative; min-height: 128px;
        transition: box-shadow .12s ease, border-color .12s ease;
    }
    [class^="st-key-small-nav-card"]:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        border-color: #0071dc;
    }
    [class^="st-key-small-nav-card"] [data-testid="stPageLink"] a {
        position: absolute; inset: 0; z-index: 2;
    }
    [class^="st-key-small-nav-card"] [data-testid="stPageLink"] a p {
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── велика кнопка: Запуск ────────────────────────────────────────────────
with st.container(border=True, key="big-nav-card"):
    st.page_link("pages/3_▶️_Запуск.py", label="Запуск розрахунку COGS", icon="▶️")
    st.markdown(
        '<div class="big-nav-caption">Обрати звіт і дату, порахувати COGS, '
        'вивантажити у Sheets</div>',
        unsafe_allow_html=True,
    )

st.write("")

# ── три менші кнопки — рівним рядом ──────────────────────────────────────
nav_items = [
    ("pages/1_🔑_Credentials.py", "Credentials", "🔑",
     "Walmart API / Google Sheets / R2"),
    ("pages/2_💲_Ціни.py", "Ціни", "💲",
     "Імпорт прайсу, ручне редагування, історія цін по SKU"),
    ("pages/4_📜_Історія.py", "Історія", "📜",
     "Усі попередні запуски розрахунку COGS (незмінні)"),
]
nav_cols = st.columns(3)
for col, (path, label, icon, help_text) in zip(nav_cols, nav_items):
    with col:
        with st.container(border=True, key=f"small-nav-card-{label}"):
            st.page_link(path, label=f"**{label}**", icon=icon)
            st.caption(help_text)
