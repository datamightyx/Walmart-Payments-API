# -*- coding: utf-8 -*-
"""Сторінка Streamlit: обрати розрахунковий період (Walmart API) і
порахувати COGS."""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from _common import (
    AdSpendImportError, ReconParseError, SheetsExportError, WalmartAPIError,
    build_item_breakdown, build_statement, build_summary, cogs_store,
    compute_cogs, export_to_sheets, format_money, get_api_client,
    get_db_connection, page_header, parse_recon_zip, resolve_payout_date,
    total_ad_spend,
)

import streamlit as st

page_header("Запуск вивантаження даних по Payments", "▶️")

conn = get_db_connection()

if "cogs_report" not in st.session_state:
    st.session_state.cogs_report = None
    st.session_state.cogs_report_source = None
    st.session_state.cogs_last_result = None

with st.container(border=True):
    st.subheader("1. Обрати звіт")
    st.caption(
        "Оберіть розрахунковий період "
        "виплати через Walmart API."
    )

    with st.form("load_form"):
        col1, col2 = st.columns(2)
        period_start = col1.date_input("Початок періоду")
        period_end = col2.date_input("Кінець періоду")

        load_clicked = st.form_submit_button("Завантажити звіт")

    if load_clicked:
        with st.status("Завантаження звіту…", expanded=True) as status:
            client = get_api_client()
            # on_progress виставляється на кешований (singleton) клієнт перед
            # кожним запуском і знімається у finally — інакше closure на цей
            # status пережив би rerun.
            client.on_progress = status.write
            try:
                available = client.available_payout_dates()
                # main.resolve_payout_date сигналізує помилку через SystemExit —
                # ловимо його нижче разом з іншими винятками.
                payout_date = resolve_payout_date(available, period_end)

                zip_bytes = client.download_recon_zip(payout_date)
                status.write("Розпакування і парсинг CSV…")
                st.session_state.cogs_report = parse_recon_zip(zip_bytes)
                st.session_state.cogs_report_source = f"API:{payout_date}"
                st.session_state.cogs_requested_period = (period_start, period_end)

                st.session_state.cogs_last_result = None
                status.update(label="Звіт завантажено", state="complete", expanded=False)
            except (WalmartAPIError, ReconParseError, ValueError, SystemExit) as exc:
                status.update(label="Помилка завантаження", state="error")
                st.error(f"Помилка: {exc}")
                st.session_state.cogs_report = None
            finally:
                client.on_progress = None

    report = st.session_state.cogs_report
    if report:
        st.success(f"Завантажено: {report.csv_name}  ({len(report)} рядків)")
        st.write(f"Період: {report.period_start} .. {report.period_end}   "
                 f"Total Payable: {format_money(report.total_payable, report.currency or 'USD')}")

        requested = st.session_state.get("cogs_requested_period")
        if requested and (report.period_start, report.period_end) != requested:
            st.warning(
                f"Запитано період {requested[0]}..{requested[1]}, а файл "
                f"покриває {report.period_start}..{report.period_end}. "
                f"Walmart видає виплату не рівно на кінець періоду — перевір, "
                f"що це той звіт, який ти очікував."
            )

if report:
    with st.container(border=True):
        st.subheader("2. Розрахувати COGS")
        default_as_of = report.period_end or date.today()
        as_of = st.date_input(
            "Ціни станом на (за замовчуванням — кінець періоду звіту)",
            value=default_as_of,
            key=f"as_of_{report.csv_name}",
        )

        if st.button("Порахувати COGS", type="primary"):
            with st.status("Розрахунок COGS…", expanded=True) as status:
                status.write(f"Агрегація юнітів і виручки по SKU, пошук цін станом на {as_of}…")
                result = compute_cogs(
                    conn, report, as_of=as_of, triggered_by="streamlit",
                    source_file=st.session_state.cogs_report_source or "",
                )
                st.session_state.cogs_last_result = result
                status.update(
                    label=f"COGS пораховано (run_id={result['run_id']})",
                    state="complete", expanded=False,
                )

result = st.session_state.cogs_last_result
if result:
    with st.container(border=True):
        st.subheader("Результат")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Юнітів (нетто)", result["total_units"])
        c2.metric("Виручка", format_money(result["total_revenue"]))
        c3.metric("COGS", format_money(result["total_cogs"]))
        margin_pct = result["gross_margin_pct"]
        c4.metric(
            "Валовий прибуток",
            format_money(result["gross_margin"]),
            f"{margin_pct:.1f}%" if margin_pct is not None else None,
        )

        if result["missing_price_skus"]:
            st.warning(
                "Немає ціни станом на обрану дату для: "
                + ", ".join(result["missing_price_skus"])
                + " — додай ціну на сторінці 💲 Ціни."
            )

        items_df = pd.DataFrame([{
            "SKU": i.sku, "Товар": i.product_name, "Юнітів": i.units,
            "Виручка": i.revenue, "Ціна/од": i.unit_cost, "COGS": i.total_cost,
            "Ціна діє з": i.price_effective_from or "—",
            "Без ціни": "⚠️" if i.price_missing else "",
        } for i in result["items"]])
        st.dataframe(
            items_df, width="stretch", hide_index=True,
            column_config={
                "Виручка": st.column_config.NumberColumn(format="$%.2f"),
                "Ціна/од": st.column_config.NumberColumn(format="$%.2f"),
                "COGS": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

        st.caption(
            f"run_id={result['run_id']} збережено в історію"
        )

    if report.period_start and report.period_end:
        with st.container(border=True):
            st.subheader("3. Реклама та експорт у Google Sheets")

            # Ключ по періоду: інший звіт/період не повинен показувати чуже
            # раніше введене значення.
            ad_spend_key = f"ad_spend_{report.period_start}_{report.period_end}"
            if ad_spend_key not in st.session_state:
                existing_extra = cogs_store.get_period_extra(
                    conn, report.period_start, report.period_end
                )
                st.session_state[ad_spend_key] = (
                    float(existing_extra["ad_spend"]) if existing_extra else 0.0
                )

            # st.caption(
            #     "Walmart Recon API рекламні витрати не віддає. Порахуй суму "
            #     "автоматично з файлу Ads-кабінету «PPC Item Performance» (файл "
            #     "не містить дати — вважається, що покриває цей самий період) "
            #     "або введи вручну."
            # )
            with st.expander(
                "Приклад файлу (Walmart Ads → Reports → PPC Item Performance)",
                expanded=True,
            ):
                st.caption(
                    "Система читає тільки колонку `Ad Spend` (сумує по всіх "
                    "рядках), решта колонок ігнорується — можна вивантажити "
                    "файл із Ads-кабінету як є, без правок."
                )
                st.dataframe(
                    pd.DataFrame([
                        {"Campaign Name": "Gauze 36pk - Phrase", "SKU Id": "HN-KDR6-YSNV",
                         "Item Name": "Premium Gauze Rolls - 36 Pack…", "Ad Spend": 354.73},
                        {"Campaign Name": "Shammy Mini - Auto", "SKU Id": "HM-OJN1-DYNW",
                         "Item Name": "Premium Mini Chamois Cloth…", "Ad Spend": 67.79},
                    ]),
                    width="stretch", hide_index=True,
                    column_config={"Ad Spend": st.column_config.NumberColumn(format="$%.2f")},
                )

            ppc_col1, ppc_col2 = st.columns([3, 1])
            uploaded_ppc = ppc_col1.file_uploader(
                "Файл PPC Item Performance (csv, будь-яка назва)", type=["csv"]
            )
            ppc_path = None
            if uploaded_ppc is not None:
                ppc_path = Path(tempfile.gettempdir()) / uploaded_ppc.name
                ppc_path.write_bytes(uploaded_ppc.getvalue())

            ppc_msg_key = f"{ad_spend_key}_ppc_msg"
            if ppc_col2.button("Порахувати з файлу", disabled=ppc_path is None):
                try:
                    total = total_ad_spend(ppc_path)
                except AdSpendImportError as exc:
                    st.session_state[ppc_msg_key] = None
                    st.error(f"Помилка: {exc}")
                else:
                    st.session_state[ad_spend_key] = total
                    st.session_state[ppc_msg_key] = (
                        f"Успішно пораховано з файлу {uploaded_ppc.name}: "
                        f"{format_money(total)}"
                    )
                    st.rerun()

            if st.session_state.get(ppc_msg_key):
                st.success(st.session_state[ppc_msg_key])

            ad_spend = st.number_input(
                "Рекламні витрати за період (Advertising Spend)",
                min_value=0.0, step=10.0, format="%.2f", key=ad_spend_key,
            )
            tacos_pct = (
                (ad_spend / result["total_revenue"] * 100)
                if result["total_revenue"] else None
            )
            st.metric(
                "TACOS %", f"{tacos_pct:.1f}%" if tacos_pct is not None else "—"
            )

            if st.button("Експортувати у Google Sheets", type="primary"):
                cogs_store.set_period_extra(
                    conn, report.period_start, report.period_end, ad_spend
                )
                summary = build_summary(report)
                statement = build_statement(summary, trailer={
                    "total_cogs": result["total_cogs"],
                    "ad_spend": ad_spend,
                    "tacos_pct": tacos_pct,
                })
                breakdown = build_item_breakdown(report)
                with st.status("Експорт у Google Sheets…", expanded=True) as status:
                    try:
                        urls = export_to_sheets(
                            statement, breakdown, report.period_label,
                            on_progress=status.write,
                        )
                    except SheetsExportError as exc:
                        status.update(label="Помилка Google Sheets", state="error")
                        st.error(f"Помилка Google Sheets: {exc}")
                    else:
                        status.update(
                            label="Вивантажено у Google Sheets",
                            state="complete", expanded=False,
                        )
                        st.success("Вивантажено у Google Sheets.")
                        st.markdown(
                            f"- [Виписка]({urls['statement_url']})\n"
                            f"- [Товари]({urls['items_url']})"
                        )
