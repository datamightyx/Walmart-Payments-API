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
    add("sales_units", "Total Units Sold", "units")
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


def _statement_values(statement: dict, units_sold: int | None = None) -> dict[str, object]:
    """Значення для однієї колонки (одного періоду), по ключах _row_template()."""
    values: dict[str, object] = {
        "paid": _money(statement["total_payable"]),
        "paid_total": _money(statement["total_payable"]),
    }
    if units_sold is not None:
        values["sales_units"] = units_sold

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
        elif kind == "units":
            cell_formats.append((rng, CellFormat(
                textFormat=TextFormat(italic=True, foregroundColor=GREY))))
            cell_formats.append((data_rng, CellFormat(
                numberFormat=NumberFormat(type="NUMBER", pattern="#,##0"),
                horizontalAlignment="RIGHT",
                textFormat=TextFormat(italic=True, foregroundColor=GREY))))
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
    spreadsheet: gspread.Spreadsheet, statement: dict, breakdown: dict, period_label: str
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

    units_sold = sum(item["units"] for item in breakdown["items"])
    values = _statement_values(statement, units_sold)

    labels = [[""]] + [[row["label"]] for row in template]
    worksheet.update(labels, "A1", value_input_option=ValueInputOption.user_entered)

    column = [[period_label]] + [[values.get(row["key"], "")] for row in template]
    worksheet.update(
        column, f"{_col_letter(target_col)}1", value_input_option=ValueInputOption.user_entered)

    cell_formats = _statement_formats(template, total_cols)
    requests = format_cell_ranges(worksheet, cell_formats)
    requests += _set_column_widths_requests(worksheet, [("A", 340)] + [
        (_col_letter(col), 260) for col in range(2, total_cols + 1)
    ])
    requests += _set_frozen_requests(worksheet, rows=1, cols=1)
    return worksheet, requests


# ── Вкладка 2: розкладка по товарах (одна вкладка, період = блок з 6 колонок,
#   найновіший — завжди C:H, старіші зсуваються праворуч через кольоровий
#   розділювач; A/B — фіксовані Товар/Item ID, спільні для всіх періодів) ──────

ITEMS_SHEET_TITLE = "Товари"
ITEM_SUBHEADER = ["Од.", "Продажі", "Комісія", "Повернення", "Fees", "Нетто"]
BLOCK_WIDTH = len(ITEM_SUBHEADER)  # 6 колонок даних на період + 1 розділювач
UNALLOC_TITLE = "Не розподілено по товарах"
UNALLOC_SUBTOTAL = "Разом не розподілено"
GRAND_TOTAL = "РАЗОМ"
SEP_BG = Color(0.80, 0.85, 0.95)


def _block_start_col(index: int) -> int:
    """1-індексована стартова колонка блоку періоду (0 = найновіший, тобто C)."""
    return 3 + index * (BLOCK_WIDTH + 1)


def _read_items_grid(worksheet: gspread.Worksheet) -> list[list]:
    if worksheet.row_count == 0:
        return []
    return worksheet.get_all_values(
        value_render_option=gspread.utils.ValueRenderOption.unformatted,
        combine_merged_cells=True,
    )


def _parse_items_registry(
    grid: list[list], num_existing_periods: int
) -> tuple[list[str], dict[str, dict], list[str], dict[str, dict]]:
    """Розбирає існуючу сітку на master-списки товарів і нерозподілених рядків.

    items[item_id] = {"name": str, "data": {block_index: [6 значень]}}
    unalloc[label]  = {"data": {block_index: значення Нетто}}
    block_index — індекс блоку в СТАРІЙ (до цього запису) розкладці колонок.
    """
    item_order: list[str] = []
    items: dict[str, dict] = {}
    unalloc_order: list[str] = []
    unalloc: dict[str, dict] = {}

    if len(grid) < 3:
        return item_order, items, unalloc_order, unalloc

    def block_slice(line: list, index: int) -> list:
        start = _block_start_col(index) - 1
        cells = line[start:start + BLOCK_WIDTH]
        return cells + [""] * (BLOCK_WIDTH - len(cells))

    row = 2
    while row < len(grid):
        line = grid[row]
        label0 = line[0] if line else ""
        if label0 in (UNALLOC_TITLE, GRAND_TOTAL):
            break
        item_id = str(line[1]) if len(line) > 1 and line[1] != "" else ""
        if item_id:
            data = {}
            for i in range(num_existing_periods):
                block = block_slice(line, i)
                if any(v not in ("", None) for v in block):
                    data[i] = block
            items[item_id] = {"name": label0, "data": data}
            item_order.append(item_id)
        row += 1

    if row < len(grid) and grid[row] and grid[row][0] == UNALLOC_TITLE:
        row += 1
        while row < len(grid):
            line = grid[row]
            label = line[0] if line else ""
            if label in (UNALLOC_SUBTOTAL, GRAND_TOTAL) or not label:
                if label in (UNALLOC_SUBTOTAL, GRAND_TOTAL):
                    break
                row += 1
                continue
            data = {}
            for i in range(num_existing_periods):
                block = block_slice(line, i)
                net_value = block[-1]
                if net_value not in ("", None):
                    data[i] = net_value
            unalloc[label] = {"data": data}
            unalloc_order.append(label)
            row += 1

    return item_order, items, unalloc_order, unalloc


def _existing_period_labels(grid: list[list]) -> list[str]:
    if not grid:
        return []
    row1 = grid[0]
    labels: list[str] = []
    i = 0
    while True:
        col = _block_start_col(i) - 1
        if col >= len(row1):
            break
        label = row1[col]
        if not label:
            break
        labels.append(str(label))
        i += 1
    return labels


def _write_items_pivot(
    spreadsheet: gspread.Spreadsheet, breakdown: dict, period_label: str
) -> tuple[gspread.Worksheet, list[dict]]:
    """Пише період як блок із 6 колонок у постійну вкладку "Товари".

    Найновіший період завжди C:H, старіші зсуваються праворуч і розділені
    кольоровою колонкою. Товар/Item ID (A/B) — спільний master-список рядків,
    зростає, коли з'являється новий товар; існуючі рядки не рухаються.
    Повторний виклик для того самого періоду перезаписує його блок.
    """
    try:
        worksheet = spreadsheet.worksheet(ITEMS_SHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=ITEMS_SHEET_TITLE, rows=5, cols=8)

    grid = _read_items_grid(worksheet)
    existing_labels = _existing_period_labels(grid)
    num_existing = len(existing_labels)
    item_order, items, unalloc_order, unalloc = _parse_items_registry(grid, num_existing)

    subtotal_data: dict[int, object] = {}
    total_data: dict[int, object] = {}
    if len(grid) >= 2:
        subtotal_row = next((r for r in grid[2:] if r and r[0] == UNALLOC_SUBTOTAL), None)
        total_row = next((r for r in grid[2:] if r and r[0] == GRAND_TOTAL), None)
        for i in range(num_existing):
            col = _block_start_col(i) - 1 + BLOCK_WIDTH - 1
            if subtotal_row and col < len(subtotal_row) and subtotal_row[col] not in ("", None):
                subtotal_data[i] = subtotal_row[col]
            if total_row and col < len(total_row) and total_row[col] not in ("", None):
                total_data[i] = total_row[col]

    if period_label in existing_labels:
        target_index = existing_labels.index(period_label)
        period_labels = list(existing_labels)
        index_map = {i: i for i in range(num_existing)}
    else:
        target_index = 0
        period_labels = [period_label] + existing_labels
        index_map = {i: i + 1 for i in range(num_existing)}

    def remap(data: dict[int, object]) -> dict[int, object]:
        return {index_map[i]: v for i, v in data.items() if i in index_map}

    for entry in items.values():
        entry["data"] = remap(entry["data"])
    for entry in unalloc.values():
        entry["data"] = remap(entry["data"])
    subtotal_data = remap(subtotal_data)
    total_data = remap(total_data)

    for item in breakdown["items"]:
        item_id = item["item_id"]
        name = item["name"] or "(без назви)"
        entry = items.setdefault(item_id, {"name": name, "data": {}})
        if name != "(без назви)" and entry["name"] in ("", "(без назви)"):
            entry["name"] = name
        if item_id not in item_order:
            item_order.append(item_id)
        entry["data"][target_index] = [
            item["units"], _money(item["product_price"]), _money(item["commission"]),
            _money(item["refunds"]), _money(item["fees"]), _money(item["net"]),
        ]

    for uentry in (breakdown.get("unallocated_breakdown") or []):
        label = uentry["label"]
        entry = unalloc.setdefault(label, {"data": {}})
        if label not in unalloc_order:
            unalloc_order.append(label)
        entry["data"][target_index] = _money(uentry["amount"])

    subtotal_data[target_index] = _money(breakdown["unallocated"])
    total_data[target_index] = _money(breakdown["total_net"])

    n = len(period_labels)
    total_cols = 2 + BLOCK_WIDTH * n + max(n - 1, 0)  # A,B + n блоків - розділювач після останнього
    blank_block = [""] * BLOCK_WIDTH

    def row_for_periods(prefix: list, per_block: Callable[[int], list]) -> list:
        line = list(prefix)
        for i in range(n):
            line += per_block(i)
            if i < n - 1:
                line.append("")
        return line

    grid_out: list[list] = []
    grid_out.append(row_for_periods(
        ["", ""], lambda i: [period_labels[i]] + [""] * (BLOCK_WIDTH - 1)))
    grid_out.append(row_for_periods(["Товар", "Item ID"], lambda i: list(ITEM_SUBHEADER)))

    for item_id in item_order:
        entry = items[item_id]
        grid_out.append(row_for_periods(
            [entry["name"] or "(без назви)", item_id],
            lambda i, e=entry: list(e["data"].get(i, blank_block))))

    grid_out.append(row_for_periods([UNALLOC_TITLE, ""], lambda i: list(blank_block)))

    for label in unalloc_order:
        entry = unalloc[label]
        grid_out.append(row_for_periods(
            [label, ""],
            lambda i, e=entry: blank_block[:-1] + [e["data"].get(i, "")]))

    grid_out.append(row_for_periods(
        [UNALLOC_SUBTOTAL, ""], lambda i: blank_block[:-1] + [subtotal_data.get(i, "")]))
    grid_out.append(row_for_periods(
        [GRAND_TOTAL, ""], lambda i: blank_block[:-1] + [total_data.get(i, "")]))

    total_rows = len(grid_out)
    worksheet.resize(rows=total_rows, cols=total_cols)
    worksheet.update(grid_out, "A1", value_input_option=ValueInputOption.user_entered)

    last_col = _col_letter(total_cols)
    cell_formats: list[tuple[str, CellFormat]] = [
        (f"A1:{last_col}2", CellFormat(
            backgroundColor=LIGHT_BG, textFormat=TextFormat(bold=True),
            horizontalAlignment="CENTER")),
    ]

    merges: list[str] = []
    widths: list[tuple[str, int]] = [("A", 320), ("B", 130)]
    for i in range(n):
        start = _block_start_col(i)
        merges.append(f"{_col_letter(start)}1:{_col_letter(start + BLOCK_WIDTH - 1)}1")
        widths.append((_col_letter(start), 55))
        for offset in range(1, BLOCK_WIDTH):
            widths.append((_col_letter(start + offset), 95))
        data_range = f"{_col_letter(start)}3:{_col_letter(start)}{total_rows}"
        cell_formats.append((data_range, CellFormat(
            numberFormat=NumberFormat(type="NUMBER", pattern="#,##0"),
            horizontalAlignment="RIGHT")))
        currency_range = (
            f"{_col_letter(start + 1)}3:{_col_letter(start + BLOCK_WIDTH - 1)}{total_rows}")
        cell_formats.append((currency_range, CellFormat(
            numberFormat=CURRENCY_FORMAT, horizontalAlignment="RIGHT")))
        if i < n - 1:
            sep_col = _col_letter(start + BLOCK_WIDTH)
            widths.append((sep_col, 20))
            cell_formats.append((f"{sep_col}1:{sep_col}{total_rows}", CellFormat(
                backgroundColor=SEP_BG)))

    title_row_idx = 3 + len(item_order)
    subtotal_row_idx = title_row_idx + 1 + len(unalloc_order)
    total_row_idx = subtotal_row_idx + 1
    cell_formats.append((f"A{title_row_idx}:{last_col}{title_row_idx}", CellFormat(
        backgroundColor=LIGHT_BG, textFormat=TextFormat(bold=True))))
    for row_idx in (subtotal_row_idx, total_row_idx):
        cell_formats.append((f"A{row_idx}:{last_col}{row_idx}", CellFormat(
            textFormat=TextFormat(bold=True), borders=TOP_BORDER)))

    requests = format_cell_ranges(worksheet, cell_formats)
    requests += _set_column_widths_requests(worksheet, widths)
    requests += _set_frozen_requests(worksheet, rows=2, cols=2)
    requests += [{
        "mergeCells": {"mergeType": "MERGE_ALL",
                       "range": gspread.utils.a1_range_to_grid_range(rng, worksheet.id)},
    } for rng in merges]
    return worksheet, requests


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

    report(f'Оновлення вкладки "{STATEMENT_SHEET_TITLE}"…')
    statement_ws, requests = _write_statement_pivot(spreadsheet, statement, breakdown, period_label)

    report(f'Оновлення вкладки "{ITEMS_SHEET_TITLE}"…')
    items_ws, items_requests = _write_items_pivot(spreadsheet, breakdown, period_label)
    requests += items_requests

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
