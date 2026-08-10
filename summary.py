# -*- coding: utf-8 -*-
"""Агрегація recon-звіту у розбивку, порівнянну з Seller Center -> Payments.

Логіка навмисно generic: групуємо по (Transaction Type, Amount Type) і
підсумовуємо Amount. Нічого не хардкодимо в whitelist, щоб новий тип
транзакції від Walmart не зникав тихо, а з'являвся в розбивці.

Звірено з UI на періоді 07/11/2026 - 07/25/2026:
    Product price     16,558.04 - 5.00 (Promo Code) = 16,553.04  -> збіг
    Net commission                          -2,485.45            -> збіг
    Other taxes (fee)                            0.34            -> збіг
    Refund product price                      -229.87            -> збіг
    Total Payable                            8,805.63            -> збіг

Увага: у UI "Product price" вже нетто від Promo Code. У CSV це два окремі
Amount Type, тому пряме порівняння одного рядка з UI не зійдеться.
"""

from __future__ import annotations

from collections import defaultdict

from .parser import ReconReport, parse_amount

# Порядок секцій у виводі — як у Seller Center.
SECTION_ORDER = ["Sale", "Refund", "Adjustment", "Service Fee"]

# ── мапінг CSV -> рядки виписки Seller Center ────────────────────────────────
# Кожен рядок: (підпис у UI, які Amount Type складати).
# None замість списку = рядок є в UI, але API його не віддає. Такі рядки
# завжди дають рівно 0 у виписці, бо в UI вони взаємознищуються:
#   Shipping +539.00 і WFS shipping reversal -539.00
#   Net tax collected 946.94 і WFS shipping tax reversal -27.32 -> 919.62
# Тобто підсумки секцій від їх відсутності не страждають.
SALES_LINES: list[tuple[str, list[str] | None]] = [
    ("Product price", ["Product Price", "Promo Code"]),
    ("Shipping", None),
    ("WFS shipping reversal", None),
    ("Net tax collected", ["Product tax"]),
    ("Other taxes (fee)", ["Other tax (Fees)"]),
    ("Net commission", ["Commission on Product"]),
    ("Net tax withheld", ["Product tax withheld"]),
    ("WFS shipping tax reversal", None),
    ("Total Walmart funded savings", ["Total Walmart Funded Savings"]),
]

REFUND_LINES: list[tuple[str, list[str] | None]] = [
    ("Product price", ["Product Price", "Promo Code"]),
    ("Shipping", None),
    ("WFS shipping reversal", None),
    ("Net tax collected", ["Product tax"]),
    ("Other taxes (fee)", ["Other tax (Fees)"]),
    ("Commission", ["Commission on Product"]),
    ("Net tax withheld", ["Product tax withheld"]),
    ("Total Walmart funded savings", ["Total Walmart Funded Savings"]),
]

# Секції, які UI зводить в один рядок "Walmart fulfillment services".
SERVICE_FEE_SECTIONS = ["Adjustment", "Service Fee", "Unknown"]

# Рядки шапки, яких у recon-файлі немає взагалі (лише в UI).
HEADER_LINES = ["Opening balance", "Reserves", "Holds"]


def build_summary(report: ReconReport) -> dict:
    """Повертає розбивку сум по типах транзакцій і типах сум."""
    by_type: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, int] = defaultdict(int)

    for row in report.rows:
        txn_type = (row.get("Transaction Type") or "Unknown").strip() or "Unknown"
        amount_type = (row.get("Amount Type") or "").strip() or "(no amount type)"
        by_type[txn_type][amount_type] += parse_amount(row.get("Amount", ""))
        counts[txn_type] += 1

    sections = {
        txn_type: dict(sorted(amounts.items()))
        for txn_type, amounts in by_type.items()
    }
    section_totals = {
        txn_type: sum(amounts.values()) for txn_type, amounts in sections.items()
    }

    return {
        "csv_name": report.csv_name,
        "period_start": report.period_start.isoformat() if report.period_start else None,
        "period_end": report.period_end.isoformat() if report.period_end else None,
        "currency": report.currency,
        "total_payable": report.total_payable,
        "transaction_count": len(report.rows),
        "counts": dict(counts),
        "sections": sections,
        "section_totals": section_totals,
        "computed_total": sum(section_totals.values()),
    }


def _build_section(
    amounts: dict[str, float], layout: list[tuple[str, list[str] | None]]
) -> tuple[list[dict], float]:
    """Розкладає Amount Type секції по рядках виписки.

    Все, що layout не забрав, додається окремими рядками в кінці — щоб новий
    Amount Type від Walmart не зник тихо і сума секції лишалась правильною.
    """
    consumed: set[str] = set()
    lines: list[dict] = []

    for label, amount_types in layout:
        if amount_types is None:
            lines.append({"label": label, "amount": 0.0, "provided": False})
            continue
        value = sum(amounts.get(t, 0.0) for t in amount_types)
        consumed.update(amount_types)
        lines.append({"label": label, "amount": value, "provided": True})

    for amount_type in sorted(set(amounts) - consumed):
        lines.append(
            {"label": amount_type, "amount": amounts[amount_type], "provided": True}
        )

    return lines, sum(amounts.values())


def build_statement(summary: dict, trailer: dict | None = None) -> dict:
    """Виписка у структурі Seller Center -> Payments.

    Рядки з provided=False друкуються як 0.00: API їх не віддає, але в UI
    вони взаємознищуються, тому підсумки від цього не змінюються.

    trailer — опціональний хвіст із COGS-даними, яких немає в recon CSV
    (Total COGS, Advertising Spend, TACOS %): {"total_cogs", "ad_spend",
    "tacos_pct"}. Рахує й підставляє викликач (webapp), тут просто
    проноситься далі до sheets_report._write_statement.
    """
    sections = summary["sections"]

    sales_lines, sales_total = _build_section(sections.get("Sale", {}), SALES_LINES)
    refund_lines, refund_total = _build_section(
        sections.get("Refund", {}), REFUND_LINES
    )

    service_total = sum(
        summary["section_totals"].get(name, 0.0) for name in SERVICE_FEE_SECTIONS
    )

    # Секції, які не потрапили ні в Sales/Refunds, ні в Service fees.
    accounted = {"Sale", "Refund", *SERVICE_FEE_SECTIONS}
    other = {
        name: total
        for name, total in summary["section_totals"].items()
        if name not in accounted
    }

    return {
        "period_start": summary["period_start"],
        "period_end": summary["period_end"],
        "currency": summary["currency"] or "USD",
        "total_payable": summary["total_payable"],
        "transaction_count": summary["transaction_count"],
        "header_lines": [
            {"label": label, "amount": 0.0, "provided": False}
            for label in HEADER_LINES
        ],
        "sections": [
            {"title": "Sales", "lines": sales_lines, "total": sales_total},
            {"title": "Refunds", "lines": refund_lines, "total": refund_total},
            {
                "title": "Services fees",
                "lines": [
                    {
                        "label": "Walmart fulfillment services",
                        "amount": service_total,
                        "provided": True,
                    }
                ],
                "total": service_total,
            },
        ]
        + (
            [
                {
                    "title": "Other",
                    "lines": [
                        {"label": name, "amount": total, "provided": True}
                        for name, total in sorted(other.items())
                    ],
                    "total": sum(other.values()),
                }
            ]
            if other
            else []
        ),
        "trailer": trailer,
    }


def build_item_breakdown(report: ReconReport) -> dict:
    """Розкладка виплати по товарах (Partner Item Id).

    Чого UI не дає взагалі. Рядки без Partner Item Id (storage fee тощо)
    не прив'язані до жодного товару — окремо і сумуються в unallocated
    (для сумісності з тим, хто просто хоче total_net), і розкладаються
    по (Transaction Type, Amount Type) у unallocated_breakdown, щоб було
    видно ЩО саме це за нарахування, а не одне безлике число.
    """
    items: dict[str, dict] = {}
    unallocated = 0.0
    unallocated_by_kind: dict[str, dict] = {}

    for row in report.rows:
        amount = parse_amount(row.get("Amount", ""))
        item_id = (row.get("Partner Item Id") or "").strip()
        if not item_id:
            unallocated += amount
            txn_type = (row.get("Transaction Type") or "").strip()
            amount_type = (row.get("Amount Type") or "").strip()
            if txn_type and amount_type:
                label = f"{txn_type} — {amount_type}"
            else:
                # Деякі службові рядки (напр. WFS RC_InventoryDisposalFee)
                # взагалі не мають Transaction/Amount Type — тоді єдиний
                # людський опис лежить у Transaction Description.
                label = (row.get("Transaction Description") or "").strip() or "Інше"
            entry = unallocated_by_kind.setdefault(
                label, {"label": label, "amount": 0.0, "count": 0}
            )
            entry["amount"] += amount
            entry["count"] += 1
            continue

        entry = items.setdefault(
            item_id,
            {
                "item_id": item_id,
                "name": "",
                "units": 0,
                "product_price": 0.0,
                "commission": 0.0,
                "refunds": 0.0,
                "fees": 0.0,
                "net": 0.0,
            },
        )

        name = (row.get("Partner Item Name") or "").strip()
        if name and not entry["name"]:
            entry["name"] = name

        txn_type = (row.get("Transaction Type") or "").strip()
        amount_type = (row.get("Amount Type") or "").strip()
        entry["net"] += amount

        if txn_type == "Sale":
            if amount_type == "Product Price":
                entry["product_price"] += amount
                entry["units"] += int(parse_amount(row.get("Ship Qty", "")))
            elif amount_type == "Commission on Product":
                entry["commission"] += amount
        elif txn_type == "Refund":
            entry["refunds"] += amount
        else:
            entry["fees"] += amount

    ordered = sorted(items.values(), key=lambda e: e["net"], reverse=True)
    unallocated_breakdown = sorted(
        unallocated_by_kind.values(), key=lambda e: abs(e["amount"]), reverse=True
    )
    return {
        "items": ordered,
        "unallocated": unallocated,
        "unallocated_breakdown": unallocated_breakdown,
        "total_net": sum(e["net"] for e in ordered) + unallocated,
    }


def _ordered_sections(sections: dict) -> list[str]:
    known = [t for t in SECTION_ORDER if t in sections]
    rest = sorted(t for t in sections if t not in SECTION_ORDER)
    return known + rest


def format_summary(summary: dict) -> str:
    """Людиночитний текстовий звіт."""
    cur = summary["currency"] or "USD"
    out = [
        "=" * 64,
        f"Період:   {summary['period_start']} .. {summary['period_end']}",
        f"Файл:     {summary['csv_name']}",
        f"Транзакцій: {summary['transaction_count']}",
        "=" * 64,
    ]

    for txn_type in _ordered_sections(summary["sections"]):
        amounts = summary["sections"][txn_type]
        count = summary["counts"].get(txn_type, 0)
        out.append(f"\n{txn_type}  ({count} рядків)")
        for amount_type, value in amounts.items():
            out.append(f"    {amount_type:<40} {value:>14,.2f}")
        out.append(f"    {'РАЗОМ':<40} {summary['section_totals'][txn_type]:>14,.2f}")

    out.append("")
    out.append("-" * 64)
    out.append(f"{'Сума транзакцій (розраховано)':<44} {summary['computed_total']:>14,.2f}")

    payable = summary["total_payable"]
    if payable is not None:
        out.append(f"{'Total Payable (з PaymentSummary)':<44} {payable:>14,.2f} {cur}")
        diff = summary["computed_total"] - payable
        # Невелика розбіжність очікувана: opening balance / reserves / holds
        # у recon-файл не потрапляють, вони є лише в UI.
        out.append(f"{'Розбіжність':<44} {diff:>14,.2f}")

    return "\n".join(out)
