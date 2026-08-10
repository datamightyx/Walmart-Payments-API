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
from gspread.utils import ValueInputOption, a1_range_to_grid_range
from gspread_formatting import Borders, Border, CellFormat, Color, NumberFormat, TextFormat
from gspread_formatting.batch_update_requests import (
    format_cell_ranges,
    set_column_widths as _set_column_widths_requests,
    set_frozen as _set_frozen_requests,
)

import config

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


def _merge_request(worksheet: gspread.Worksheet, a1_range: str) -> dict:
    return {
        "mergeCells": {
            "mergeType": "MERGE_ALL",
            "range": a1_range_to_grid_range(a1_range, worksheet.id),
        }
    }


# ── Вкладка 1: виписка ────────────────────────────────────────────────────────

def _write_statement(worksheet: gspread.Worksheet, statement: dict) -> list[dict]:
    """Пише значення одразу, форматування повертає як список requests (не виконує)."""
    rows: list[list] = []
    row_kinds: list[str] = []   # паралельно rows: "title"|"paid"|"section"|"line"|"line_muted"|"total"|"note"

    rows.append([f"{statement['period_start']} – {statement['period_end']}", ""])
    row_kinds.append("title")
    rows.append(["Paid to you", _money(statement["total_payable"])])
    row_kinds.append("paid")
    rows.append(["", ""])
    row_kinds.append("blank")

    for line in statement["header_lines"]:
        label = line["label"] + (" *" if not line["provided"] else "")
        rows.append([label, _money(line["amount"])])
        row_kinds.append("line" if line["provided"] else "line_muted")

    for section in statement["sections"]:
        rows.append([section["title"], ""])
        row_kinds.append("section")
        for line in section["lines"]:
            label = line["label"] + (" *" if not line["provided"] else "")
            rows.append([label, _money(line["amount"])])
            row_kinds.append("line" if line["provided"] else "line_muted")
        rows.append(["Total", _money(section["total"])])
        row_kinds.append("total")

    rows.append(["", ""])
    row_kinds.append("blank")
    rows.append(["Paid to you:", _money(statement["total_payable"])])
    row_kinds.append("total")
    rows.append([
        "* Рядок є в Seller Center, але Walmart Recon API його не віддає. "
        "Такі позиції взаємознищуються в UI, тому підсумки збігаються з ним "
        "до копійки.",
        "",
    ])
    row_kinds.append("note")

    trailer = statement.get("trailer")
    if trailer:
        rows.append(["", ""])
        row_kinds.append("blank")
        rows.append(["Total COGS", _money(trailer["total_cogs"])])
        row_kinds.append("total")
        rows.append(["", ""])
        row_kinds.append("blank")
        rows.append(["Advertising (Spend)", _money(trailer["ad_spend"])])
        row_kinds.append("line")
        tacos_pct = trailer.get("tacos_pct")
        rows.append(["TACOS %", (tacos_pct / 100) if tacos_pct is not None else ""])
        row_kinds.append("percent")

    worksheet.update(rows, "A1", value_input_option=ValueInputOption.user_entered)

    cell_formats: list[tuple[str, CellFormat]] = []
    merges: list[str] = []

    for index, kind in enumerate(row_kinds, start=1):
        rng = f"A{index}:B{index}"
        if kind == "title":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(bold=True, fontSize=13, foregroundColor=BLUE))))
        elif kind == "paid":
            cell_formats.append((f"A{index}", CellFormat(
                textFormat=TextFormat(bold=True, fontSize=10, foregroundColor=BLUE))))
            cell_formats.append((f"B{index}", CellFormat(
                textFormat=TextFormat(bold=True, fontSize=16),
                numberFormat=CURRENCY_FORMAT,
                horizontalAlignment="RIGHT")))
        elif kind == "section":
            cell_formats.append((rng, CellFormat(
                backgroundColor=LIGHT_BG,
                textFormat=TextFormat(bold=True, fontSize=11))))
        elif kind == "line":
            cell_formats.append((f"B{index}", CellFormat(
                numberFormat=CURRENCY_FORMAT, horizontalAlignment="RIGHT")))
        elif kind == "line_muted":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(italic=True, foregroundColor=GREY))))
            cell_formats.append((f"B{index}", CellFormat(
                numberFormat=CURRENCY_FORMAT, horizontalAlignment="RIGHT",
                textFormat=TextFormat(italic=True, foregroundColor=GREY))))
        elif kind == "total":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(bold=True), borders=TOP_BORDER)))
            cell_formats.append((f"B{index}", CellFormat(
                textFormat=TextFormat(bold=True), numberFormat=CURRENCY_FORMAT,
                horizontalAlignment="RIGHT", borders=TOP_BORDER)))
        elif kind == "percent":
            cell_formats.append((rng, CellFormat(textFormat=TextFormat(bold=True))))
            cell_formats.append((f"B{index}", CellFormat(
                textFormat=TextFormat(bold=True),
                numberFormat=NumberFormat(type="PERCENT", pattern="0.0%"),
                horizontalAlignment="RIGHT")))
        elif kind == "note":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(italic=True, fontSize=9, foregroundColor=GREY),
                wrapStrategy="WRAP")))
            merges.append(rng)

    requests = format_cell_ranges(worksheet, cell_formats)
    requests += _set_column_widths_requests(worksheet, [("A", 340), ("B", 140)])
    requests += _set_frozen_requests(worksheet, rows=0)
    requests += [_merge_request(worksheet, rng) for rng in merges]
    return requests


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

    statement_rows = 6 + len(statement["header_lines"]) + sum(
        2 + len(section["lines"]) for section in statement["sections"]
    ) + (5 if statement.get("trailer") else 0)
    unallocated_breakdown = breakdown.get("unallocated_breakdown") or []
    items_rows = 2 + len(breakdown["items"]) + (
        2 + len(unallocated_breakdown) if unallocated_breakdown else 0
    )

    report(f'Перестворення вкладки "{period_label} Виписка"…')
    statement_ws = _reset_worksheet(
        spreadsheet, f"{period_label} Виписка", rows=statement_rows + 5, cols=2)
    report(f'Перестворення вкладки "{period_label} Товари"…')
    items_ws = _reset_worksheet(
        spreadsheet, f"{period_label} Товари", rows=items_rows + 5,
        cols=len(ITEM_HEADER))

    report("Запис виписки…")
    requests = _write_statement(statement_ws, statement)
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
