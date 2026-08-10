# -*- coding: utf-8 -*-
"""Розрахунок COGS з уже розібраного ReconReport.

Юніти й виручка рахуються з рядків Sale/Refund з Amount Type == "Product
Price" (те саме, чим оперує summary.build_item_breakdown, але рахуємо
самі — щоб не тягнути залежність і мати повний контроль над netto-юнітами
для повернень).

Ціна на юніт береться "станом на as_of" (за замовчуванням — кінець періоду
звіту), а не поточна: так зміна ціни в майбутньому не змінює вже пораховані
періоди. Кожен виклик пише незмінний знімок у cogs_runs/cogs_run_items.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from . import store
from ..parser import ReconReport, parse_amount


@dataclass
class CogsItemResult:
    sku: str
    product_name: str
    units: int
    revenue: float
    unit_cost: float | None
    total_cost: float
    price_effective_from: str | None
    price_missing: bool


def _aggregate_units_revenue(report: ReconReport) -> dict[str, dict]:
    """SKU -> {units (sale - refund), revenue, name}."""
    agg: dict[str, dict] = {}
    for row in report.rows:
        if (row.get("Amount Type") or "").strip() != "Product Price":
            continue
        txn_type = (row.get("Transaction Type") or "").strip()
        if txn_type not in ("Sale", "Refund"):
            continue

        sku = (row.get("Partner Item Id") or "").strip()
        if not sku:
            continue

        entry = agg.setdefault(sku, {"units": 0, "revenue": 0.0, "name": ""})
        name = (row.get("Partner Item Name") or "").strip()
        if name and not entry["name"]:
            entry["name"] = name

        qty = int(parse_amount(row.get("Ship Qty", "")))
        amount = parse_amount(row.get("Amount", ""))
        entry["revenue"] += amount
        entry["units"] += qty if txn_type == "Sale" else -qty

    return agg


def compute_cogs(
    conn: sqlite3.Connection,
    report: ReconReport,
    as_of: date | None = None,
    triggered_by: str = "cli",
    source_file: str = "",
) -> dict:
    """Рахує COGS по товарах, пише незмінний run у БД, повертає підсумок."""
    as_of = as_of or report.period_end or date.today()
    agg = _aggregate_units_revenue(report)

    items: list[CogsItemResult] = []
    missing: list[str] = []
    for sku in sorted(agg):
        data = agg[sku]
        price_row = store.latest_price(conn, sku, as_of)
        if price_row is None:
            unit_cost = None
            effective_from = None
            missing.append(sku)
        else:
            unit_cost = price_row["unit_cost"]
            effective_from = price_row["effective_from"]

        units = data["units"]
        total_cost = (unit_cost or 0.0) * units
        items.append(CogsItemResult(
            sku=sku,
            product_name=data["name"],
            units=units,
            revenue=data["revenue"],
            unit_cost=unit_cost,
            total_cost=total_cost,
            price_effective_from=effective_from,
            price_missing=price_row is None,
        ))

    total_units = sum(i.units for i in items)
    total_revenue = sum(i.revenue for i in items)
    total_cogs = sum(i.total_cost for i in items)

    run_id = store.insert_run(
        conn,
        run_at=datetime.now().isoformat(timespec="seconds"),
        period_start=report.period_start.isoformat() if report.period_start else None,
        period_end=report.period_end.isoformat() if report.period_end else None,
        payout_date=report.csv_name,
        source_file=source_file,
        as_of_date=as_of.isoformat(),
        total_units=total_units,
        total_revenue=total_revenue,
        total_cogs=total_cogs,
        missing_price_skus=",".join(missing),
        triggered_by=triggered_by,
    )
    store.insert_run_items(conn, run_id, [
        {
            "sku": i.sku,
            "product_name": i.product_name,
            "units": i.units,
            "unit_cost": i.unit_cost,
            "total_cost": i.total_cost,
            "revenue": i.revenue,
            "price_effective_from": i.price_effective_from,
            "price_missing": int(i.price_missing),
        }
        for i in items
    ])

    gross_margin = total_revenue - total_cogs
    return {
        "run_id": run_id,
        "as_of": as_of.isoformat(),
        "period_start": report.period_start.isoformat() if report.period_start else None,
        "period_end": report.period_end.isoformat() if report.period_end else None,
        "total_units": total_units,
        "total_revenue": total_revenue,
        "total_cogs": total_cogs,
        "gross_margin": gross_margin,
        "gross_margin_pct": (gross_margin / total_revenue * 100) if total_revenue else None,
        "missing_price_skus": missing,
        "items": items,
    }
