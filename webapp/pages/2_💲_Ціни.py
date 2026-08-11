# -*- coding: utf-8 -*-
"""Сторінка Streamlit: ціни для COGS — імпорт з xlsx, ручне редагування, історія."""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from _common import (
    PriceImportError, cogs_store, config, get_db_connection, import_from_xlsx,
    page_header,
)

import streamlit as st

page_header("Ціни для COGS", "💲")

conn = get_db_connection()

MONEY_COLUMN = st.column_config.NumberColumn(format="$%.2f")

# ── поточні ціни (нагорі, виділено) ──────────────────────────────────────
st.markdown(
    """
    <div style="background-color:#eaf4ff;border-left:5px solid #0071dc;
    border-radius:6px;padding:10px 16px;margin-bottom:6px;">
    <span style="font-size:1.05rem;font-weight:600;color:#0071dc;">
    💲 Поточні ціни</span>
    <span style="color:#555;"> — актуальний unit cost, який зараз
    використовується для розрахунку COGS.</span>
    </div>
    """,
    unsafe_allow_html=True,
)
with st.container(border=True):
    current = cogs_store.current_prices(conn)
    if current:
        df = pd.DataFrame([{
            "SKU": r["sku"], "Товар": r["product_name"], "Item ID": r["item_id"],
            "ASIN": r["asin"], "Ціна": r["unit_cost"], "Діє з": r["effective_from"],
            "Джерело": r["source"],
        } for r in current])
        st.dataframe(
            df, width="stretch", hide_index=True,
            column_config={"Ціна": MONEY_COLUMN},
        )
    else:
        st.info("Ще немає жодної ціни в базі.")

# ── імпорт з xlsx ────────────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("Імпорт прайсу з Excel")
    st.caption(
        "Для SKU, яких ще немає в базі, ціна діятиме \"завжди\" (з 1900-01-01) — "
        "щоб коректно порахувати вже завантажені історичні звіти. Для SKU з "
        "новою ціною стара версія лишається і продовжує діяти для минулих дат."
    )
    with st.expander("Приклад файлу (аркуш COGS)", expanded=True):
        st.caption(
            "Обов'язкові колонки: `Product`, `SKU`, `Total price/unit`. "
            "`amazon ASIN` і `Item ID` — опційні, просто зберігаються поруч."
        )
        st.dataframe(
            pd.DataFrame([
                {"Product": "Shammy Mini", "amazon ASIN": "B07TW7STZY",
                 "SKU": "HM-OJN1-DYNW", "Item ID": 2540475484, "Total price/unit": 2.49},
                {"Product": "Shammy Original - 2 pack", "amazon ASIN": "B08HZ5SMF4",
                 "SKU": "FV-G2BM-YDNW", "Item ID": 2003467732, "Total price/unit": 3.74},
            ]),
            width="stretch", hide_index=True,
            column_config={"Total price/unit": MONEY_COLUMN},
        )

    default_price_file = config.PACKAGE_DIR / "Walmart - Payments.xlsx"
    source_options = []
    if default_price_file.exists():
        source_options.append(f"Файл у проєкті ({default_price_file.name})")
    source_options.append("Завантажити інший файл")

    source_choice = st.radio("Джерело", source_options, horizontal=True)
    sheet_name = st.text_input("Назва аркуша", value="COGS")
    eff_date = st.date_input(
        "Дата дії нової ціни (для вже відомих SKU)", value=date.today(),
    )

    xlsx_path: Path | None = None
    if source_choice.startswith("Файл у проєкті"):
        xlsx_path = default_price_file
    else:
        uploaded = st.file_uploader("xlsx-файл з прайсом", type=["xlsx"])
        if uploaded is not None:
            tmp = Path(tempfile.gettempdir()) / f"cogs_price_upload_{uploaded.name}"
            tmp.write_bytes(uploaded.getvalue())
            xlsx_path = tmp

    if xlsx_path is not None:
        try:
            preview = import_from_xlsx(
                conn, xlsx_path, sheet=sheet_name, effective_from=eff_date, dry_run=True,
            )
        except PriceImportError as exc:
            st.error(str(exc))
            preview = []

        if preview:
            changed = [c for c in preview if c.action != "unchanged"]
            df = pd.DataFrame([{
                "SKU": c.sku, "Товар": c.product_name, "Дія": c.action,
                "Стара ціна": c.old_price, "Нова ціна": c.new_price,
                "Діє з": c.effective_from,
            } for c in preview])
            st.dataframe(
                df, width="stretch", hide_index=True,
                column_config={"Стара ціна": MONEY_COLUMN, "Нова ціна": MONEY_COLUMN},
            )
            st.write(f"Змін: {len(changed)} з {len(preview)}")

            if changed and st.button("Підтвердити імпорт", type="primary"):
                import_from_xlsx(
                    conn, xlsx_path, sheet=sheet_name, effective_from=eff_date,
                    dry_run=False,
                )
                st.success(f"Імпортовано {len(changed)} змін.")
                st.rerun()
            elif not changed:
                st.info("Немає змін порівняно з поточними цінами.")

# ── ручне додавання/редагування ──────────────────────────────────────────
with st.container(border=True):
    st.subheader("Додати / оновити ціну вручну")
    with st.form("manual_price_form"):
        col1, col2 = st.columns(2)
        sku = col1.text_input("SKU (= Partner Item Id у звіті)")
        product_name = col2.text_input("Назва товару")
        col3, col4 = st.columns(2)
        unit_cost = col3.number_input("Ціна за одиницю", min_value=0.0, step=0.01, format="%.2f")
        manual_eff_date = col4.date_input("Діє з", value=date.today(), key="manual_eff_date")
        manual_submit = st.form_submit_button("Зберегти ціну")

    if manual_submit:
        if not sku.strip():
            st.error("SKU обов'язковий.")
        else:
            sku_clean = sku.strip()
            existing = cogs_store.price_history(conn, sku_clean)
            last = existing[0] if existing else None
            is_new = last is None
            eff = cogs_store.SENTINEL_DATE if is_new else manual_eff_date
            cogs_store.insert_price_version(
                conn, sku=sku_clean,
                item_id=last["item_id"] if last else None,
                asin=last["asin"] if last else None,
                product_name=product_name.strip() or (last["product_name"] if last else ""),
                unit_cost=unit_cost,
                effective_from=eff, source="manual",
            )
            st.success(f"Збережено ціну {sku} = {unit_cost:.2f} (діє з {eff.isoformat()}).")
            st.rerun()

# ── історія ціни по SKU ───────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("Історія ціни по SKU")
    skus = cogs_store.all_skus(conn)
    if skus:
        chosen_sku = st.selectbox("SKU", skus)
        history = cogs_store.price_history(conn, chosen_sku)
        hist_df = pd.DataFrame([{
            "Діє з": r["effective_from"], "Ціна": r["unit_cost"],
            "Джерело": r["source"], "Створено": r["created_at"],
        } for r in history])
        st.dataframe(
            hist_df, width="stretch", hide_index=True,
            column_config={"Ціна": MONEY_COLUMN},
        )
