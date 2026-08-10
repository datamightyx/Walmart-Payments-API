# -*- coding: utf-8 -*-
"""CLI для вивантаження Walmart Payments (recon / settlement) звітів.

Запускати з кореня проєкту (каталог, де лежить цей файл):

Приклади:
    # які періоди виплат доступні
    python main.py list
    python main.py list --limit 10

    # найсвіжіша виплата
    python main.py fetch --latest

    # конкретна дата виплати (як у списку, MMDDYYYY)
    python main.py fetch --date 07282026

    # за розрахунковим періодом — сам знайде відповідну дату виплати
    python main.py fetch --period 2026-07-11:2026-07-25

    # тільки підсумки, без збереження файлів
    python main.py fetch --latest --no-save
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import config
from api import WalmartAPIError, WalmartPaymentsAPI
from parser import (
    ReconParseError, ReconReport, parse_recon_csv, parse_recon_zip,
)
from summary import (
    build_item_breakdown, build_statement, build_summary, format_summary,
)

PAYOUT_DATE_FORMAT = "%m%d%Y"      # MMDDYYYY — формат availableReconFiles
ISO_DATE_FORMAT = "%Y-%m-%d"

logger = logging.getLogger("walmartPayments")


def _configure_console() -> None:
    """Консоль Windows за замовчуванням cp1251 і падає на назвах товарів."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _payout_date_to_date(payout_date: str) -> date:
    return datetime.strptime(payout_date, PAYOUT_DATE_FORMAT).date()


def resolve_payout_date(available: list[str], period_end: date) -> str:
    """Знаходить дату виплати для періоду, що закінчується period_end.

    Виплата йде через кілька днів після кінця періоду, тому беремо найближчу
    доступну дату, яка не раніша за period_end. Якщо таких немає (період ще
    не виплачений) — кидаємо помилку замість тихого повернення чужого файлу.
    """
    candidates = sorted(
        (d for d in available if _payout_date_to_date(d) >= period_end),
        key=_payout_date_to_date,
    )
    if not candidates:
        raise SystemExit(
            f"Немає доступної виплати для періоду, що закінчується "
            f"{period_end:%Y-%m-%d}. Найсвіжіша доступна: "
            f"{max(available, key=_payout_date_to_date) if available else 'жодної'}"
        )
    return candidates[0]


def save_report(
    zip_bytes: bytes,
    report: ReconReport,
    summary: dict,
    output_dir: Path,
) -> list[Path]:
    """Кладе на диск сирий ZIP, розпакований CSV і JSON з підсумками."""
    output_dir.mkdir(parents=True, exist_ok=True)
    label = report.period_label
    written: list[Path] = []

    zip_path = output_dir / f"walmart_payments_{label}.zip"
    zip_path.write_bytes(zip_bytes)
    written.append(zip_path)

    csv_path = output_dir / f"walmart_payments_{label}.csv"
    with zipfile.ZipFile(zip_path) as archive:
        csv_path.write_bytes(archive.read(report.csv_name))
    written.append(csv_path)

    json_path = output_dir / f"walmart_payments_{label}_summary.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    written.append(json_path)

    return written


def save_pdf(report: ReconReport, summary: dict, output_dir: Path) -> Path:
    """Малює PDF-виписку у вигляді Seller Center + сторінку по товарах."""
    # Імпорт локальний: reportlab потрібен лише для --pdf, решта CLI має
    # працювати і без нього.
    from pdf_report import build_pdf

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"walmart_payments_{report.period_label}.pdf"
    return build_pdf(
        build_statement(summary), build_item_breakdown(report), pdf_path
    )


def save_sheets(report: ReconReport, summary: dict) -> dict:
    """Пише виписку + розкладку по товарах у Google Sheets."""
    # Імпорт локальний: gspread потрібен лише для --sheets.
    from sheets_report import export_to_sheets

    return export_to_sheets(
        build_statement(summary), build_item_breakdown(report), report.period_label
    )


def cmd_list(args: argparse.Namespace) -> int:
    client = WalmartPaymentsAPI()
    dates = client.available_payout_dates()
    if not dates:
        print("Walmart не повернув жодної дати виплати.")
        return 1

    shown = dates[: args.limit] if args.limit else dates
    print(f"Доступно дат виплат: {len(dates)} (показано {len(shown)})")
    print(f"{'дата виплати':<14} {'--date':<10}")
    for payout_date in shown:
        human = _payout_date_to_date(payout_date)
        print(f"{human:%Y-%m-%d}     {payout_date}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    client = WalmartPaymentsAPI()

    if args.date:
        payout_date = args.date
    else:
        available = client.available_payout_dates()
        if not available:
            print("Walmart не повернув жодної дати виплати.")
            return 1
        if args.latest:
            payout_date = available[0]
        else:
            _, period_end = args.period
            payout_date = resolve_payout_date(available, period_end)
        logger.info("Обрано дату виплати %s", payout_date)

    zip_bytes = client.download_recon_zip(payout_date)
    report = parse_recon_zip(zip_bytes)
    summary = build_summary(report)

    print(format_summary(summary))

    if args.period:
        requested_start, requested_end = args.period
        if (report.period_start, report.period_end) != (requested_start, requested_end):
            print(
                f"\nУВАГА: запитано {requested_start}..{requested_end}, "
                f"файл покриває {report.period_start}..{report.period_end}"
            )

    written: list[Path] = []
    if not args.no_save:
        written = save_report(zip_bytes, report, summary, args.output_dir)
    if args.pdf:
        written.append(save_pdf(report, summary, args.output_dir))

    if written:
        print("\nЗбережено:")
        for path in written:
            print(f"  {path}")

    if args.sheets:
        from sheets_report import SheetsExportError
        try:
            urls = save_sheets(report, summary)
            print(f"\nGoogle Sheets:\n  {urls['statement_url']}\n  {urls['items_url']}")
        except SheetsExportError as exc:
            print(f"\nПОМИЛКА Google Sheets: {exc}", file=sys.stderr)
            return 1

    return 0


def cmd_pdf(args: argparse.Namespace) -> int:
    """PDF із уже завантаженого файлу — без жодного звернення до API."""
    path: Path = args.file
    if not path.exists():
        print(f"Файл не знайдено: {path}", file=sys.stderr)
        return 1

    try:
        if path.suffix.lower() == ".zip":
            report = parse_recon_zip(path.read_bytes())
        else:
            report = parse_recon_csv(
                path.name, path.read_text(encoding="utf-8-sig", errors="replace")
            )
    except PermissionError:
        # Excel тримає відкритий CSV в ексклюзивному режимі, і Windows не дає
        # його навіть прочитати.
        zip_hint = path.with_suffix(".zip")
        message = f"Файл заблоковано іншою програмою (найчастіше Excel): {path}"
        if zip_hint.exists():
            message += f"\nЗакрий його або візьми zip: --file {zip_hint}"
        else:
            message += "\nЗакрий програму, що тримає файл, і повтори."
        print(message, file=sys.stderr)
        return 1

    summary = build_summary(report)
    print(format_summary(summary))

    pdf_path = save_pdf(report, summary, args.output_dir)
    print(f"\nЗбережено:\n  {pdf_path}")

    if args.sheets:
        from sheets_report import SheetsExportError
        try:
            urls = save_sheets(report, summary)
            print(f"\nGoogle Sheets:\n  {urls['statement_url']}\n  {urls['items_url']}")
        except SheetsExportError as exc:
            print(f"\nПОМИЛКА Google Sheets: {exc}", file=sys.stderr)
            return 1

    return 0


def _parse_date_arg(raw: str) -> date:
    try:
        return datetime.strptime(raw.strip(), ISO_DATE_FORMAT).date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Очікується YYYY-MM-DD, отримано {raw!r}"
        ) from exc


def _load_report_from_file(path: Path) -> ReconReport:
    if path.suffix.lower() == ".zip":
        return parse_recon_zip(path.read_bytes())
    return parse_recon_csv(
        path.name, path.read_text(encoding="utf-8-sig", errors="replace")
    )


def cmd_cogs_import_prices(args: argparse.Namespace) -> int:
    from cogs import store as cogs_store
    from cogs.price_import import PriceImportError, import_from_xlsx

    if not args.file.exists():
        print(f"Файл не знайдено: {args.file}", file=sys.stderr)
        return 1

    conn = cogs_store.connect(config.COGS_DB_PATH)
    try:
        changes = import_from_xlsx(
            conn, args.file, sheet=args.sheet, effective_from=args.effective_from,
        )
    except PriceImportError as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        return 1

    changed = [c for c in changes if c.action != "unchanged"]
    for ch in changed:
        print(
            f"{ch.action:<8} {ch.sku:<16} "
            f"{(ch.old_price if ch.old_price is not None else 0):>8.2f} -> "
            f"{ch.new_price:>8.2f}  (діє з {ch.effective_from})"
        )
    print(f"\nОновлено цін: {len(changed)} з {len(changes)} (БД: {config.COGS_DB_PATH})")
    return 0


def cmd_cogs_compute(args: argparse.Namespace) -> int:
    from cogs import store as cogs_store
    from cogs.calculator import compute_cogs

    if not args.file.exists():
        print(f"Файл не знайдено: {args.file}", file=sys.stderr)
        return 1

    report = _load_report_from_file(args.file)
    conn = cogs_store.connect(config.COGS_DB_PATH)
    result = compute_cogs(
        conn, report, as_of=args.as_of, triggered_by="cli", source_file=str(args.file)
    )

    print(f"Період: {result['period_start']} .. {result['period_end']}   "
          f"ціни станом на {result['as_of']}")
    print(f"Юнітів (нетто): {result['total_units']}")
    print(f"Виручка (Product Price): {result['total_revenue']:.2f}")
    print(f"COGS: {result['total_cogs']:.2f}")
    if result["gross_margin_pct"] is not None:
        print(f"Валовий прибуток: {result['gross_margin']:.2f} "
              f"({result['gross_margin_pct']:.1f}%)")
    if result["missing_price_skus"]:
        print(f"\nУВАГА: немає ціни станом на {result['as_of']} для: "
              f"{', '.join(result['missing_price_skus'])}")
    print(f"\nЗбережено run_id={result['run_id']} у {config.COGS_DB_PATH}")
    return 0


def _parse_period(raw: str) -> tuple[date, date]:
    try:
        start_raw, end_raw = raw.split(":", 1)
        start = datetime.strptime(start_raw.strip(), ISO_DATE_FORMAT).date()
        end = datetime.strptime(end_raw.strip(), ISO_DATE_FORMAT).date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Очікується YYYY-MM-DD:YYYY-MM-DD, отримано {raw!r}"
        ) from exc
    if start > end:
        raise argparse.ArgumentTypeError("Початок періоду пізніше за кінець")
    return start, end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="walmartPayments",
        description="Вивантаження Walmart Payments (recon) звітів через API",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="докладний лог")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="показати доступні дати виплат")
    p_list.add_argument("--limit", type=int, default=20,
                        help="скільки останніх показати (0 = всі)")
    p_list.set_defaults(func=cmd_list)

    p_fetch = sub.add_parser("fetch", help="завантажити звіт")
    target = p_fetch.add_mutually_exclusive_group(required=True)
    target.add_argument("--date", help="дата виплати MMDDYYYY, напр. 07282026")
    target.add_argument("--period", type=_parse_period,
                        help="розрахунковий період YYYY-MM-DD:YYYY-MM-DD")
    target.add_argument("--latest", action="store_true",
                        help="найсвіжіша доступна виплата")
    p_fetch.add_argument("--output-dir", type=Path,
                         default=config.DEFAULT_OUTPUT_DIR,
                         help="куди складати файли")
    p_fetch.add_argument("--no-save", action="store_true",
                         help="не зберігати zip/csv/json")
    p_fetch.add_argument("--pdf", action="store_true",
                         help="додатково зібрати PDF-виписку як у Seller Center")
    p_fetch.add_argument("--sheets", action="store_true",
                         help="додатково вивантажити у Google Sheets "
                              "(потрібен credentials/service_account.json "
                              "і WALMART_PAYMENTS_GOOGLE_SHEET_ID)")
    p_fetch.set_defaults(func=cmd_fetch)

    p_pdf = sub.add_parser(
        "pdf", help="PDF з уже завантаженого csv/zip, без звернення до API")
    p_pdf.add_argument("--file", type=Path, required=True,
                       help="шлях до .csv або .zip recon-звіту")
    p_pdf.add_argument("--output-dir", type=Path,
                       default=config.DEFAULT_OUTPUT_DIR,
                       help="куди покласти PDF")
    p_pdf.add_argument("--sheets", action="store_true",
                       help="додатково вивантажити у Google Sheets "
                            "(потрібен credentials/service_account.json "
                            "і WALMART_PAYMENTS_GOOGLE_SHEET_ID)")
    p_pdf.set_defaults(func=cmd_pdf)

    p_cogs = sub.add_parser("cogs", help="COGS: імпорт цін і розрахунок собівартості")
    cogs_sub = p_cogs.add_subparsers(dest="cogs_command", required=True)

    p_cogs_import = cogs_sub.add_parser(
        "import-prices", help="імпортувати ціни з xlsx (аркуш COGS)")
    p_cogs_import.add_argument("--file", type=Path, required=True,
                               help="шлях до xlsx з прайсом")
    p_cogs_import.add_argument("--sheet", default="COGS",
                               help="назва аркуша (дефолт COGS)")
    p_cogs_import.add_argument(
        "--effective-from", type=_parse_date_arg, default=None,
        help="з якої дати діє нова ціна для вже відомого SKU (дефолт — сьогодні); "
             "для нового SKU завжди 1900-01-01, незалежно від цього параметра")
    p_cogs_import.set_defaults(func=cmd_cogs_import_prices)

    p_cogs_compute = cogs_sub.add_parser(
        "compute", help="порахувати COGS з уже завантаженого csv/zip")
    p_cogs_compute.add_argument("--file", type=Path, required=True,
                                help="шлях до .csv або .zip recon-звіту")
    p_cogs_compute.add_argument(
        "--as-of", type=_parse_date_arg, default=None,
        help="ціни станом на цю дату (дефолт — кінець періоду звіту)")
    p_cogs_compute.set_defaults(func=cmd_cogs_compute)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # --period існує лише у fetch; для list підставляємо None, щоб спільний
    # код нижче міг звертатись до нього без getattr-хаків.
    if not hasattr(args, "period"):
        args.period = None

    try:
        return args.func(args)
    except (WalmartAPIError, ReconParseError) as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        # Типово: попередній csv/pdf відкритий в Excel або переглядачі PDF.
        print(
            f"ПОМИЛКА: файл заблоковано іншою програмою: {exc.filename}\n"
            f"Закрий його (Excel / переглядач PDF) і повтори.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
