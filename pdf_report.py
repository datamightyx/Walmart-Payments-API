# -*- coding: utf-8 -*-
"""Генерація PDF-виписки Walmart Payments у вигляді, як у Seller Center.

Сторінка 1 — виписка 1-в-1 як в UI: шапка з періодом і сумою виплати,
секції Sales / Refunds / Services fees з підсумками.
Сторінка 2 — розкладка по товарах, чого UI не дає.

Рядки, яких API не віддає (Shipping, WFS shipping reversal, Opening balance,
Reserves, Holds), друкуються як 0.00 — так само, як у UI на цьому періоді.
Вони позначені виноскою, щоб нуль не читався як «Walmart нічого не нарахував».
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = "WPArial"
FONT_BOLD = "WPArial-Bold"

NAVY = colors.HexColor("#1a2b4a")
GREY = colors.HexColor("#666666")
LIGHT = colors.HexColor("#dddddd")
RED = colors.HexColor("#c0392b")
BLUE = colors.HexColor("#0071dc")      # фірмовий синій Walmart

_fonts_ready = False


def _register_fonts() -> None:
    """Arial потрібен заради кирилиці — вбудовані шрифти її не мають."""
    global _fonts_ready
    if _fonts_ready:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(FONT_DIR / "arial.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / "arialbd.ttf")))
    _fonts_ready = True


def _styles() -> dict:
    return {
        "period": ParagraphStyle("period", fontName=FONT_BOLD, fontSize=12,
                                  textColor=BLUE, leading=15),
        "paid_label": ParagraphStyle("paid_label", fontName=FONT_REGULAR,
                                      fontSize=8.5, textColor=BLUE,
                                      alignment=TA_RIGHT, leading=11),
        "paid_value": ParagraphStyle("paid_value", fontName=FONT_BOLD,
                                      fontSize=19, textColor=colors.black,
                                      alignment=TA_RIGHT, leading=23),
        "section": ParagraphStyle("section", fontName=FONT_BOLD, fontSize=11.5,
                                   textColor=colors.black, leading=14,
                                   spaceBefore=12, spaceAfter=4),
        "title": ParagraphStyle("title", fontName=FONT_BOLD, fontSize=15,
                                 textColor=NAVY, leading=19, spaceAfter=2),
        "sub": ParagraphStyle("sub", fontName=FONT_REGULAR, fontSize=9,
                               textColor=GREY, leading=12, spaceAfter=10),
        "note": ParagraphStyle("note", fontName=FONT_REGULAR, fontSize=7.8,
                                textColor=GREY, leading=10.5),
    }


def _money(value: float, currency: str = "") -> str:
    """Формат як у Seller Center: -$1,234.56."""
    sign = "-" if value < 0 else ""
    body = f"${abs(value):,.2f}"
    return f"{sign}{body} {currency}".strip()


def _line_rows(lines: list[dict]) -> tuple[list[list], list[int]]:
    """Рядки таблиці + індекси тих, що позначені виноскою."""
    rows, marked = [], []
    for index, line in enumerate(lines):
        label = line["label"]
        if not line["provided"]:
            label += " *"
            marked.append(index)
        rows.append([label, _money(line["amount"])])
    return rows, marked


def _amount_table(rows: list[list], marked: list[int], width: float) -> Table:
    table = Table(rows, colWidths=[width * 0.68, width * 0.32])
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 9.3),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
    ]
    for index, row in enumerate(rows):
        if row[1].startswith("-"):
            style.append(("TEXTCOLOR", (1, index), (1, index), RED))
    for index in marked:
        style.append(("TEXTCOLOR", (0, index), (1, index), GREY))
    table.setStyle(TableStyle(style))
    return table


def _total_row(label: str, value: float, width: float) -> Table:
    table = Table([[label, _money(value)]],
                  colWidths=[width * 0.68, width * 0.32])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9.8),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TEXTCOLOR", (1, 0), (1, 0), RED if value < 0 else colors.black),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
    ]))
    return table


def _statement_story(statement: dict, width: float, styles: dict) -> list:
    story: list = []
    currency = statement["currency"]

    header = Table(
        [[
            Paragraph(
                f"{statement['period_start']} &ndash; {statement['period_end']}",
                styles["period"],
            ),
            [
                Paragraph("Paid to you", styles["paid_label"]),
                Paragraph(
                    _money(statement["total_payable"] or 0.0, currency),
                    styles["paid_value"],
                ),
            ],
        ]],
        colWidths=[width * 0.55, width * 0.45],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.7, color=LIGHT))
    story.append(Spacer(1, 10))

    rows, marked = _line_rows(statement["header_lines"])
    story.append(_amount_table(rows, marked, width))

    for section in statement["sections"]:
        story.append(Paragraph(section["title"], styles["section"]))
        rows, marked = _line_rows(section["lines"])
        story.append(_amount_table(rows, marked, width))
        story.append(_total_row("Total:", section["total"], width))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.7, color=LIGHT))
    story.append(Spacer(1, 6))
    story.append(_total_row("Paid to you:", statement["total_payable"] or 0.0, width))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "* Рядок є в Seller Center, але Walmart Recon API його не віддає. "
        "У виписці такі позиції взаємознищуються (Shipping і WFS shipping "
        "reversal дають нуль), тому підсумки секцій і сума виплати збігаються "
        "з UI до копійки.",
        styles["note"],
    ))

    return story


def _items_story(breakdown: dict, width: float, styles: dict) -> list:
    story: list = [
        Paragraph("Розкладка по товарах", styles["title"]),
        Paragraph(
            "Дані transaction-level з recon-звіту. ",
            styles["sub"],
        ),
    ]

    cell = ParagraphStyle("cell", fontName=FONT_REGULAR, fontSize=7.2, leading=8.8)

    header = ["Товар", "Item ID", "Од.", "Продажі", "Комісія", "Повернення",
              "Fees", "Нетто"]
    rows: list[list] = [header]

    for item in breakdown["items"]:
        name = item["name"] or "(без назви)"
        if len(name) > 58:
            name = name[:57] + "…"
        rows.append([
            Paragraph(name, cell),
            item["item_id"],
            str(item["units"]),
            _money(item["product_price"]),
            _money(item["commission"]),
            _money(item["refunds"]),
            _money(item["fees"]),
            _money(item["net"]),
        ])

    if breakdown["unallocated"]:
        rows.append([
            Paragraph("Не розподілено по товарах (storage fee тощо)", cell),
            "", "", "", "", "", "", _money(breakdown["unallocated"]),
        ])

    rows.append(["РАЗОМ", "", "", "", "", "", "", _money(breakdown["total_net"])])

    # Item ID — це SKU виду "LQ-NFKV-9NEW3", тому колонка ширша за очікувану,
    # інакше текст налазить на сусідню.
    col_widths = [
        width * 0.255, width * 0.145, width * 0.055,
        width * 0.105, width * 0.10, width * 0.105, width * 0.105, width * 0.115,
    ]
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 7.2),
        ("FONTNAME", (0, -1), (-1, -1), FONT_BOLD),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f5f9")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, colors.HexColor("#fafafa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for index, row in enumerate(rows[1:], start=1):
        if isinstance(row[-1], str) and row[-1].startswith("-"):
            style.append(("TEXTCOLOR", (-1, index), (-1, index), RED))
    table.setStyle(TableStyle(style))

    story.append(table)
    return story


def build_pdf(statement: dict, breakdown: dict, output_path: Path) -> Path:
    """Малює двосторінковий PDF і повертає шлях до нього."""
    _register_fonts()
    styles = _styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Walmart Payments {statement['period_start']} - "
              f"{statement['period_end']}",
    )
    width = doc.width

    story = _statement_story(statement, width, styles)
    story.append(PageBreak())
    story.extend(_items_story(breakdown, width, styles))

    doc.build(story)
    return output_path
