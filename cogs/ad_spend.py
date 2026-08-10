# -*- coding: utf-8 -*-
"""Сума Advertising Spend з Walmart Ads "PPC Item Performance" CSV.

Файл не містить дати/періоду — Walmart не пише його в цей export, лише
Campaign/Ad Group/SKU-рядки з колонкою `Ad Spend` за той діапазон дат, який
був обраний при вивантаженні в Ads-кабінеті. Тому period тут не визначається
з файлу: викликач (webapp) сам вирішує, до якого періоду (period_extras)
приписати суму — типово той самий, що й завантажений recon-звіт.
"""

from __future__ import annotations

import csv
from pathlib import Path

REQUIRED_COLUMNS = {"Ad Spend"}


class AdSpendImportError(RuntimeError):
    """csv не схожий на Walmart Ads "PPC Item Performance" звіт."""


def total_ad_spend(path: str | Path) -> float:
    """Сума колонки `Ad Spend` по всіх рядках файлу."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = REQUIRED_COLUMNS - set(header)
        if missing:
            raise AdSpendImportError(
                f"У {path.name} немає колонок: {sorted(missing)}. "
                f"Це не PPC Item Performance звіт?"
            )
        total = 0.0
        for row in reader:
            raw = (row.get("Ad Spend") or "").strip()
            if raw:
                total += float(raw)
    return round(total, 2)
