# -*- coding: utf-8 -*-
"""Парсер recon-звіту Walmart (ZIP -> структуровані рядки).

Особливості формату, знайдені на реальному файлі:

1. ZIP містить рівно один CSV:
       Kivals_LLC_10001051452_MP_07282026_reconciliationreport_v1.csv
2. Рядок 0 — заголовок (43 колонки).
3. Рядок 1 — сміттєвий трейлер виду "Number of Lines in file 5280".
   Якщо його не викинути, csv.DictReader зробить з нього фальшивий рядок
   даних, де Period Start Date == "Number of Lines in file 5280".
4. Один рядок має Transaction Type == "PaymentSummary" — саме там лежать
   Period Start/End Date, Total Payable і Currency. У транзакційних рядках
   ці колонки порожні.
5. Кодування трапляється не чисте UTF-8 (назви товарів), тому декодуємо
   з errors="replace".
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

TRAILER_MARKER = "Number of Lines in file"
PAYMENT_SUMMARY_TYPE = "PaymentSummary"
CSV_DATE_FORMAT = "%m/%d/%Y"


class ReconParseError(RuntimeError):
    """Файл не схожий на recon-звіт Walmart."""


@dataclass
class ReconReport:
    """Розібраний recon-звіт за один період виплати."""

    csv_name: str
    period_start: date | None
    period_end: date | None
    total_payable: float | None
    currency: str
    deposit_description: str
    rows: list[dict] = field(default_factory=list)

    @property
    def period_label(self) -> str:
        if self.period_start and self.period_end:
            return f"{self.period_start:%Y-%m-%d}_{self.period_end:%Y-%m-%d}"
        return "unknown-period"

    def __len__(self) -> int:
        return len(self.rows)


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, CSV_DATE_FORMAT).date()
    except ValueError:
        return None


def parse_amount(raw: str) -> float:
    """Amount у CSV — рядок; порожній/невалідний трактуємо як 0."""
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def extract_csv(zip_bytes: bytes) -> tuple[str, str]:
    """Дістає єдиний CSV із ZIP. Повертає (ім'я, текст)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ReconParseError(f"Відповідь не є валідним ZIP: {exc}") from exc

    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise ReconParseError(f"У ZIP немає CSV, лише: {archive.namelist()}")

    name = names[0]
    text = archive.read(name).decode("utf-8-sig", errors="replace")
    return name, text


def parse_recon_csv(csv_name: str, csv_text: str) -> ReconReport:
    """Розбирає текст CSV у ReconReport, відкидаючи трейлер."""
    lines = csv_text.splitlines()
    if not lines:
        raise ReconParseError("CSV порожній")

    header, *body = lines
    body = [ln for ln in body if not ln.startswith(TRAILER_MARKER)]

    reader = csv.DictReader([header, *body])

    summary_row: dict | None = None
    rows: list[dict] = []
    for row in reader:
        if (row.get("Transaction Type") or "").strip() == PAYMENT_SUMMARY_TYPE:
            summary_row = row
        else:
            rows.append(row)

    if summary_row is None:
        raise ReconParseError(
            f"У {csv_name} немає рядка PaymentSummary — файл неповний або "
            f"формат змінився"
        )

    total_payable_raw = (summary_row.get("Total Payable") or "").strip()

    return ReconReport(
        csv_name=csv_name,
        period_start=_parse_date(summary_row.get("Period Start Date", "")),
        period_end=_parse_date(summary_row.get("Period End Date", "")),
        total_payable=parse_amount(total_payable_raw) if total_payable_raw else None,
        currency=(summary_row.get("Currency") or "").strip(),
        deposit_description=(
            summary_row.get("Transaction Description") or ""
        ).strip(),
        rows=rows,
    )


def parse_recon_zip(zip_bytes: bytes) -> ReconReport:
    """ZIP-байти -> ReconReport."""
    csv_name, csv_text = extract_csv(zip_bytes)
    return parse_recon_csv(csv_name, csv_text)
