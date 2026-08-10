# -*- coding: utf-8 -*-
"""Конфігурація walmartPayments.

Значення беруться з environment variables, а якщо їх нема — з дефолтів нижче.
Це дозволяє тримати ключі поза кодом на проді, не ламаючи локальний запуск.
"""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ENV_FILE = PACKAGE_DIR / ".env"

# .env поруч з пакетом — сюди пише сторінка "Креди" веб-інтерфейсу. Має бути
# завантажений ДО перших os.getenv нижче, інакше збережені там креди не
# підхопляться, поки Python-процес явно не отримає їх у середовищі.
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    pass

# ── Walmart Marketplace credentials ──────────────────────────────────────────
# Без дефолтів у коді — тільки .env / env var / (на Streamlit Cloud) st.secrets
# (webapp/_common.py переносить st.secrets у os.environ до цього імпорту).
CLIENT_ID = os.getenv("WALMART_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("WALMART_CLIENT_SECRET", "")
SELLER_ID = os.getenv("WALMART_SELLER_ID", "")

# ── API ──────────────────────────────────────────────────────────────────────
BASE_URL = "https://marketplace.walmartapis.com"
REPORT_VERSION = "v1"

# Walmart віддає recon-файл бінарним стрімом. Будь-який інший Accept
# (application/json, text/csv, */*) дає або 406, або 200 з тілом-помилкою
# "No acceptable representation". Перевірено емпірично.
RECON_ACCEPT = "application/octet-stream"

# ── Rate limiting ────────────────────────────────────────────────────────────
# Walmart віддає 429 вже після кількох запитів підряд. MIN_REQUEST_INTERVAL —
# м'яка пауза між будь-якими двома викликами; THROTTLE_COOLDOWN — довга пауза
# після реального 429 (вимога: 15-20 хвилин).
MIN_REQUEST_INTERVAL_SECONDS = 45
THROTTLE_COOLDOWN_SECONDS = 900
MAX_RETRIES = 3

TOKEN_TTL_SECONDS = 900          # Walmart видає токен на 900с
TOKEN_REFRESH_MARGIN_SECONDS = 60

HTTP_TIMEOUT_SECONDS = 120

# ── Файли ────────────────────────────────────────────────────────────────────
DEFAULT_OUTPUT_DIR = Path(
    os.getenv("WALMART_PAYMENTS_OUTPUT_DIR", PACKAGE_DIR / "reports")
)

# SQLite з цінами (версійованими) та історією запусків COGS.
COGS_DB_PATH = Path(
    os.getenv("WALMART_PAYMENTS_COGS_DB", PACKAGE_DIR / "cogs" / "cogs.db")
)

# ── Google Sheets export ─────────────────────────────────────────────────────
# Service account JSON-ключ (Google Cloud Console -> IAM -> Service Accounts).
# Таблицю треба розшарити на email цього service account (роль Editor).
# Фіксований шлях у проєкті (не env var) — на прод-деплої абсолютний шлях з
# іншої машини все одно нічого не означає; файл просто кладеться в
# credentials/ поруч з пакетом і їде разом з кодом.
GOOGLE_CREDENTIALS_FILE = PACKAGE_DIR / "credentials" / "service_account.json"
# ID таблиці з URL: https://docs.google.com/spreadsheets/d/<ЦЕЙ ID>/edit
GOOGLE_SHEET_ID = os.getenv("WALMART_PAYMENTS_GOOGLE_SHEET_ID", "")

# ── Cloudflare R2 (опційно) — синхронізація cogs.db, щоб пережити redeploy на
# ефемерному хостингу. Всі 4 задані -> увімкнено (cogs/r2_sync.py).
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_OBJECT_KEY = os.getenv("R2_OBJECT_KEY", "cogs.db")

# Зсув між кінцем розрахункового періоду і датою виплати. Спостережено 3 дні
# (період 07/11-07/25/2026 -> файл 07282026). Використовується лише як
# підказка при резолві періоду; фактичний вибір іде по списку доступних дат.
PAYOUT_LAG_DAYS = 3
