# -*- coding: utf-8 -*-
"""Вивантаження Walmart Payments у Google Sheets тим самим виглядом, що й PDF.

Один спредшит, дві вкладки на кожен розрахунковий період (назва = period_label
з main.py, як і в іменах файлів zip/csv/pdf):
    "<label> Виписка"  — те саме, що сторінка 1 pdf_report.py
    "<label> Товари"   — те саме, що сторінка 2 pdf_report.py (розкладка по товарах)

Повторний виклик для того самого періоду перезаписує вкладки, а не плодить
дублікати.

Усе форматування (кольори, bold, currency, freeze, merge) збирається в один
список Sheets API requests і відправляється ОДНИМ batch_update. Перша версія
цього модуля робила виклик format_cell_range на кожен рядок окремо (кожен —
окремий HTTP POST) і на виписці з ~30 рядків впиралась у квоту Google Sheets
API "Write requests per minute per user" (429). Функції з
gspread_formatting.batch_update_requests повертають словники запитів, не
виконуючи їх, — саме вони тут і використані замість format_cell_range/
set_frozen/set_column_widths з публічного gspread_formatting.

Потрібен service account JSON (config.GOOGLE_CREDENTIALS_FILE) і таблиця,
розшарена на його email з правом Editor (config.GOOGLE_SHEET_ID).
"""

from __future__ import annotations

from typing import Callable

import gspread
from gspread.utils import ValueInputOption
from gspread_formatting import Borders, Border, CellFormat, Color, NumberFormat, TextFormat
from gspread_formatting.batch_update_requests import (
    format_cell_ranges,
    set_column_widths as _set_column_widths_requests,
    set_frozen as _set_frozen_requests,
)

import config
from summary import HEADER_LINES, REFUND_LINES, SALES_LINES

STATEMENT_SHEET_TITLE = "Виписка"

GREY = Color(0.40, 0.40, 0.40)
BLUE = Color(0.0, 0.44, 0.86)     # фірмовий синій Walmart
LIGHT_BG = Color(0.95, 0.96, 0.98)

CURRENCY_FORMAT = NumberFormat(type="CURRENCY", pattern='$#,##0.00;[RED]-$#,##0.00')
TOP_BORDER = Borders(top=Border("SOLID", color=Color(0.7, 0.7, 0.7)))


class SheetsExportError(RuntimeError):
    """Помилка вивантаження у Google Sheets."""


def _client() -> gspread.Client:
    try:
        return gspread.service_account(filename=config.GOOGLE_CREDENTIALS_FILE)
    except FileNotFoundError as exc:
        raise SheetsExportError(
            f"Не знайдено service account JSON: {config.GOOGLE_CREDENTIALS_FILE} "
            f"— поклади файл ключа саме туди (credentials/service_account.json)."
        ) from exc


def _open_spreadsheet(client: gspread.Client) -> gspread.Spreadsheet:
    if not config.GOOGLE_SHEET_ID:
        raise SheetsExportError(
            "Не задано WALMART_PAYMENTS_GOOGLE_SHEET_ID "
            "(ID таблиці з її URL)."
        )
    try:
        return client.open_by_key(config.GOOGLE_SHEET_ID)
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise SheetsExportError(
            f"Таблицю {config.GOOGLE_SHEET_ID} не знайдено, або service "
            f"account не має до неї доступу (розшар на Editor)."
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise SheetsExportError(f"Google Sheets API помилка: {exc}") from exc


def _reset_worksheet(
    spreadsheet: gspread.Spreadsheet, title: str, rows: int, cols: int
) -> gspread.Worksheet:
    """Перестворює вкладку начисто, щоб не лишалось слідів попереднього запису."""
    try:
        worksheet = spreadsheet.worksheet(title)
        spreadsheet.del_worksheet(worksheet)
    except gspread.exceptions.WorksheetNotFound:
        pass
    return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def _money(value: float) -> float:
    """Числом, а не рядком — щоб CURRENCY-формат і сортування в Sheets працювали."""
    return round(value or 0.0, 2)


# ── Вкладка 1: виписка (одна вкладка, період = колонка, B = найновіший) ────────

NOTE_TEXT = (
    "* Рядок є в Seller Center, але Walmart Recon API його не віддає. "
    "Такі позиції взаємознищуються в UI, тому підсумки збігаються з ним до копійки."
)


def _col_letter(col: int) -> str:
    """1 -> 'A', 2 -> 'B', 27 -> 'AA'."""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _row_template() -> list[dict]:
    """Фіксований (однаковий для всіх періодів) список рядків вкладки "Виписка".

    "provided" для HEADER_LINES/SALES_LINES/REFUND_LINES визначається складом
    самого layout (amount_types is None), а не даними конкретного періоду —
    тому статус line/line_muted можна зашити в шаблон один раз.
    """
    template: list[dict] = []

    def add(key: str, label: str, kind: str) -> None:
        template.append({"key": key, "label": label, "kind": kind})

    add("paid", "Paid to you", "paid")
    add("blank1", "", "blank")

    for label in HEADER_LINES:
        add(f"header:{label}", f"{label} *", "line_muted")

    add("sales_title", "Sales", "section")
    for label, amount_types in SALES_LINES:
        muted = amount_types is None
        add(f"sales:{label}", f"{label} *" if muted else label, "line_muted" if muted else "line")
    add("sales_total", "Total", "total")

    add("refunds_title", "Refunds", "section")
    for label, amount_types in REFUND_LINES:
        muted = amount_types is None
        add(f"refund:{label}", f"{label} *" if muted else label, "line_muted" if muted else "line")
    add("refunds_total", "Total", "total")

    add("services_title", "Services fees", "section")
    add("services_line", "Walmart fulfillment services", "line")
    add("services_total", "Total", "total")

    add("blank2", "", "blank")
    add("paid_total", "Paid to you:", "total")
    add("note", NOTE_TEXT, "note")

    add("blank3", "", "blank")
    add("total_cogs", "Total COGS", "total")
    add("blank4", "", "blank")
    add("ad_spend", "Advertising (Spend)", "line")
    add("blank5", "", "blank")
    add("profit", "Profit", "total")
    add("tacos", "TACOS %", "percent")

    return template


def _statement_values(statement: dict) -> dict[str, object]:
    """Значення для однієї колонки (одного періоду), по ключах _row_template()."""
    values: dict[str, object] = {
        "paid": _money(statement["total_payable"]),
        "paid_total": _money(statement["total_payable"]),
    }

    for label, line in zip(HEADER_LINES, statement["header_lines"]):
        values[f"header:{label}"] = _money(line["amount"])

    sections_by_title = {section["title"]: section for section in statement["sections"]}

    sales = sections_by_title.get("Sales")
    if sales:
        for (label, _), line in zip(SALES_LINES, sales["lines"]):
            values[f"sales:{label}"] = _money(line["amount"])
        values["sales_total"] = _money(sales["total"])

    refunds = sections_by_title.get("Refunds")
    if refunds:
        for (label, _), line in zip(REFUND_LINES, refunds["lines"]):
            values[f"refund:{label}"] = _money(line["amount"])
        values["refunds_total"] = _money(refunds["total"])

    services = sections_by_title.get("Services fees")
    if services:
        values["services_line"] = _money(services["lines"][0]["amount"])
        values["services_total"] = _money(services["total"])

    trailer = statement.get("trailer")
    if trailer:
        values["total_cogs"] = _money(trailer["total_cogs"])
        values["ad_spend"] = _money(trailer["ad_spend"])
        values["profit"] = _money(
            statement["total_payable"] - trailer["total_cogs"] - trailer["ad_spend"])
        tacos_pct = trailer.get("tacos_pct")
        values["tacos"] = (tacos_pct / 100) if tacos_pct is not None else ""

    return values


def _ensure_statement_worksheet(
    spreadsheet: gspread.Spreadsheet, template: list[dict]
) -> gspread.Worksheet:
    try:
        worksheet = spreadsheet.worksheet(STATEMENT_SHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=STATEMENT_SHEET_TITLE, rows=len(template) + 1, cols=2)
    if worksheet.row_count < len(template) + 1:
        worksheet.add_rows(len(template) + 1 - worksheet.row_count)
    return worksheet


def _statement_formats(template: list[dict], total_cols: int) -> list[tuple[str, CellFormat]]:
    last_col = _col_letter(total_cols)
    cell_formats: list[tuple[str, CellFormat]] = [
        (f"A1:{last_col}1", CellFormat(
            textFormat=TextFormat(bold=True, fontSize=11, foregroundColor=BLUE),
            horizontalAlignment="CENTER")),
    ]

    for offset, row in enumerate(template, start=2):
        kind = row["kind"]
        rng = f"A{offset}:{last_col}{offset}"
        data_rng = f"B{offset}:{last_col}{offset}"
        if kind == "paid":
            cell_formats.append((f"A{offset}", CellFormat(
                textFormat=TextFormat(bold=True, fontSize=10, foregroundColor=BLUE))))
            cell_formats.append((data_rng, CellFormat(
                textFormat=TextFormat(bold=True, fontSize=16),
                numberFormat=CURRENCY_FORMAT,
                horizontalAlignment="RIGHT")))
        elif kind == "section":
            cell_formats.append((rng, CellFormat(
                backgroundColor=LIGHT_BG,
                textFormat=TextFormat(bold=True, fontSize=11))))
        elif kind == "line":
            cell_formats.append((data_rng, CellFormat(
                numberFormat=CURRENCY_FORMAT, horizontalAlignment="RIGHT")))
        elif kind == "line_muted":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(italic=True, foregroundColor=GREY))))
            cell_formats.append((data_rng, CellFormat(
                numberFormat=CURRENCY_FORMAT, horizontalAlignment="RIGHT",
                textFormat=TextFormat(italic=True, foregroundColor=GREY))))
        elif kind == "total":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(bold=True), borders=TOP_BORDER)))
            cell_formats.append((data_rng, CellFormat(
                textFormat=TextFormat(bold=True), numberFormat=CURRENCY_FORMAT,
                horizontalAlignment="RIGHT", borders=TOP_BORDER)))
        elif kind == "percent":
            cell_formats.append((rng, CellFormat(textFormat=TextFormat(bold=True))))
            cell_formats.append((data_rng, CellFormat(
                textFormat=TextFormat(bold=True),
                numberFormat=NumberFormat(type="PERCENT", pattern="0.0%"),
                horizontalAlignment="RIGHT")))
        elif kind == "note":
            cell_formats.append((f"A{offset}", CellFormat(
                textFormat=TextFormat(italic=True, fontSize=9, foregroundColor=GREY),
                wrapStrategy="WRAP")))

    return cell_formats


def _write_statement_pivot(
    spreadsheet: gspread.Spreadsheet, statement: dict, period_label: str
) -> tuple[gspread.Worksheet, list[dict]]:
    """Пише період як колонку в постійну вкладку "Виписка" (B = найновіший).

    Повторний виклик для того самого періоду перезаписує його колонку.
    Новий період вставляється в B, старі колонки зсуваються праворуч без ліміту.
    """
    template = _row_template()
    worksheet = _ensure_statement_worksheet(spreadsheet, template)

    header_row = worksheet.row_values(1)
    existing_periods = header_row[1:]  # від колонки B

    if period_label in existing_periods:
        target_col = existing_periods.index(period_label) + 2
        total_cols = 1 + len(existing_periods)
    else:
        if existing_periods:
            spreadsheet.batch_update({"requests": [{
                "insertDimension": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "COLUMNS",
                        "startIndex": 1,
                        "endIndex": 2,
                    },
                    "inheritFromBefore": False,
                },
            }]})
        target_col = 2
        total_cols = 2 + len(existing_periods)

    values = _statement_values(statement)

    labels = [[""]] + [[row["label"]] for row in template]
    worksheet.update(labels, "A1", value_input_option=ValueInputOption.user_entered)

    column = [[period_label]] + [[values.get(row["key"], "")] for row in template]
    worksheet.update(
        column, f"{_col_letter(target_col)}1", value_input_option=ValueInputOption.user_entered)

    cell_formats = _statement_formats(template, total_cols)
    requests = format_cell_ranges(worksheet, cell_formats)
    requests += _set_column_widths_requests(worksheet, [("A", 340)] + [
        (_col_letter(col), 130) for col in range(2, total_cols + 1)
    ])
    requests += _set_frozen_requests(worksheet, rows=1, cols=1)
    return worksheet, requests


# ── Вкладка 2: розкладка по товарах ────────────────────────────────────────────

ITEM_HEADER = ["Товар", "Item ID", "Од.", "Продажі", "Комісія", "Повернення",
               "Fees", "Нетто"]


def _write_items(worksheet: gspread.Worksheet, breakdown: dict) -> list[dict]:
    rows: list[list] = [ITEM_HEADER]
    row_kinds: list[str] = ["header"]  # паралельно rows[1:]

    for item in breakdown["items"]:
        rows.append([
            item["name"] or "(без назви)",
            item["item_id"],
            item["units"],
            _money(item["product_price"]),
            _money(item["commission"]),
            _money(item["refunds"]),
            _money(item["fees"]),
            _money(item["net"]),
        ])
        row_kinds.append("item")

    unallocated_breakdown = breakdown.get("unallocated_breakdown") or []
    if unallocated_breakdown:
        rows.append(["Не розподілено по товарах", "", "", "", "", "", "", ""])
        row_kinds.append("section")
        for entry in unallocated_breakdown:
            label = entry["label"]
            if entry["count"] > 1:
                label += f" ({entry['count']} рядк.)"
            rows.append([label, "", "", "", "", "", "", _money(entry["amount"])])
            row_kinds.append("item")
        rows.append([
            "Разом не розподілено", "", "", "", "", "", "", _money(breakdown["unallocated"]),
        ])
        row_kinds.append("subtotal")

    rows.append(["РАЗОМ", "", "", "", "", "", "", _money(breakdown["total_net"])])
    row_kinds.append("subtotal")

    worksheet.update(rows, "A1", value_input_option=ValueInputOption.user_entered)

    cell_formats: list[tuple[str, CellFormat]] = []

    header_range = f"A1:{chr(ord('A') + len(ITEM_HEADER) - 1)}1"
    cell_formats.append((header_range, CellFormat(
        backgroundColor=LIGHT_BG,
        textFormat=TextFormat(bold=True),
        horizontalAlignment="CENTER")))

    last_row = len(rows)
    for col in "DEFGH":
        cell_formats.append((f"{col}2:{col}{last_row}", CellFormat(
            numberFormat=CURRENCY_FORMAT, horizontalAlignment="RIGHT")))
    cell_formats.append((f"C2:C{last_row}", CellFormat(horizontalAlignment="RIGHT")))

    for index, kind in enumerate(row_kinds, start=1):
        rng = f"A{index}:H{index}"
        if kind == "section":
            cell_formats.append((rng, CellFormat(
                backgroundColor=LIGHT_BG,
                textFormat=TextFormat(bold=True))))
        elif kind == "subtotal":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(bold=True), borders=TOP_BORDER)))

    requests = format_cell_ranges(worksheet, cell_formats)
    requests += _set_column_widths_requests(worksheet, [
        ("A", 320), ("B", 130), ("C", 55),
        ("D", 100), ("E", 90), ("F", 100), ("G", 90), ("H", 100),
    ])
    requests += _set_frozen_requests(worksheet, rows=1)
    return requests


# ── Публічний вхід ─────────────────────────────────────────────────────────────

def export_to_sheets(
    statement: dict,
    breakdown: dict,
    period_label: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Пише виписку й розкладку по товарах у дві вкладки спільної таблиці.

    on_progress(message) викликається перед кожним мережевим кроком (напр.
    щоб показати статус у Streamlit) — необов'язковий, CLI його не передає.

    Повертає {"spreadsheet_url", "statement_url", "items_url"} — прямі
    посилання на вкладки (з #gid=...), щоб їх можна було одразу відкрити.
    """
    def report(message: str) -> None:
        if on_progress:
            on_progress(message)

    report("Підключення до Google Sheets…")
    client = _client()
    report("Відкриття таблиці…")
    spreadsheet = _open_spreadsheet(client)

    unallocated_breakdown = breakdown.get("unallocated_breakdown") or []
    items_rows = 2 + len(breakdown["items"]) + (
        2 + len(unallocated_breakdown) if unallocated_breakdown else 0
    )

    report(f'Оновлення вкладки "{STATEMENT_SHEET_TITLE}"…')
    statement_ws, requests = _write_statement_pivot(spreadsheet, statement, period_label)

    report(f'Перестворення вкладки "{period_label} Товари"…')
    items_ws = _reset_worksheet(
        spreadsheet, f"{period_label} Товари", rows=items_rows + 5,
        cols=len(ITEM_HEADER))

    report("Запис розкладки по товарах…")
    requests += _write_items(items_ws, breakdown)

    try:
        if requests:
            report("Застосування форматування…")
            spreadsheet.batch_update({"requests": requests})
    except gspread.exceptions.APIError as exc:
        raise SheetsExportError(f"Google Sheets API помилка форматування: {exc}") from exc

    report("Готово.")

    base_url = spreadsheet.url
    return {
        "spreadsheet_url": base_url,
        "statement_url": f"{base_url}#gid={statement_ws.id}",
        "items_url": f"{base_url}#gid={items_ws.id}",
    }
