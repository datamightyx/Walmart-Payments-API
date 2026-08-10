# -*- coding: utf-8 -*-
"""walmartPayments — автономне вивантаження Walmart Payments (recon) звітів.

Самодостатній пакет: не залежить від adReport та інших частин репозиторію.

Програмне використання:
    from walmartPayments import WalmartPaymentsAPI, parse_recon_zip, build_summary

    client = WalmartPaymentsAPI()
    payout_date = client.available_payout_dates()[0]
    report = parse_recon_zip(client.download_recon_zip(payout_date))
    print(build_summary(report)["total_payable"])
"""

from .api import WalmartAPIError, WalmartPaymentsAPI
from .parser import ReconParseError, ReconReport, parse_recon_csv, parse_recon_zip
from .summary import build_summary, format_summary

__all__ = [
    "WalmartPaymentsAPI",
    "WalmartAPIError",
    "ReconReport",
    "ReconParseError",
    "parse_recon_zip",
    "parse_recon_csv",
    "build_summary",
    "format_summary",
]
