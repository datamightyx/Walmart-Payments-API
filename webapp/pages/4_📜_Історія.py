# -*- coding: utf-8 -*-
"""Сторінка Streamlit: історія запусків розрахунку COGS (лише читання —
записи в cogs_runs/cogs_run_items незмінні)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from _common import cogs_store, get_db_connection, page_header

import streamlit as st

page_header("Історія запусків", "📜")

conn = get_db_connection()
runs = cogs_store.list_runs(conn)

if not runs:
    st.info("Ще немає жодного запуску. Порахуй COGS на сторінці ▶️ Запуск.")
    st.stop()

MONEY_COLUMN = st.column_config.NumberColumn(format="$%.2f")

with st.container(border=True):
    st.caption(
        "Кожен рядок — незмінний знімок на момент розрахунку: ціни, що діяли "
        "тоді, зафіксовані назавжди й не міняються при подальшому оновленні "
        "прайсу."
    )

    runs_df = pd.DataFrame([{
        "run_id": r["run_id"],
        "Коли": r["run_at"],
        "Період": f"{r['period_start']} .. {r['period_end']}",
        "Ціни станом на": r["as_of_date"],
        "Юнітів": r["total_units"],
        "Виручка": r["total_revenue"],
        "COGS": r["total_cogs"],
        "Маржа": r["total_revenue"] - r["total_cogs"],
        "Запущено": r["triggered_by"],
        "Без ціни": r["missing_price_skus"] or "",
    } for r in runs])
    st.dataframe(
        runs_df, width="stretch", hide_index=True,
        column_config={"Виручка": MONEY_COLUMN, "COGS": MONEY_COLUMN, "Маржа": MONEY_COLUMN},
    )

with st.container(border=True):
    st.subheader("Деталі запуску")
    run_ids = [r["run_id"] for r in runs]
    chosen = st.selectbox("run_id", run_ids)

    if chosen:
        run = cogs_store.get_run(conn, chosen)
        items = cogs_store.get_run_items(conn, chosen)
        st.write(
            f"**Період:** {run['period_start']} .. {run['period_end']}  "
            f"**Ціни станом на:** {run['as_of_date']}"
        )
        st.write(
            f"**Джерело:** {run['source_file'] or '—'}  "
            f"**Запущено:** {run['triggered_by']} о {run['run_at']}"
        )

        items_df = pd.DataFrame([{
            "SKU": i["sku"], "Товар": i["product_name"], "Юнітів": i["units"],
            "Виручка": i["revenue"], "Ціна/од": i["unit_cost"],
            "COGS": i["total_cost"], "Ціна діяла з": i["price_effective_from"] or "—",
            "Без ціни": "⚠️" if i["price_missing"] else "",
        } for i in items])
        st.dataframe(
            items_df, width="stretch", hide_index=True,
            column_config={"Виручка": MONEY_COLUMN, "Ціна/од": MONEY_COLUMN, "COGS": MONEY_COLUMN},
        )
