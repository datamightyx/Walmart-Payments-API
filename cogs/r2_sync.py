# -*- coding: utf-8 -*-
"""Опційна синхронізація cogs.db з Cloudflare R2 (S3-сумісний object storage).

Навіщо: на Streamlit Community Cloud файлова система ефемерна — cogs.db
(ціни, історія запусків) зникає при кожному redeploy/рестарті контейнера.
Якщо задані R2_* env vars — store.py тягне базу з R2 при connect() і заливає
назад після кожного запису, тож стан переживає redeploy.

Без R2_* — цей модуль неактивний (enabled() == False), локальний sqlite
файл поводиться як раніше.

boto3 імпортується лінькво: якщо його нема в оточенні, а R2 не налаштований,
requirements не роздуваються дарма локально/у CLI-only сценаріях.
"""

from __future__ import annotations

import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(
        config.R2_ACCOUNT_ID and config.R2_ACCESS_KEY_ID
        and config.R2_SECRET_ACCESS_KEY and config.R2_BUCKET
    )


def _client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def download_db(local_path: Path) -> bool:
    """Тягне cogs.db з R2 поверх local_path (перед відкриттям з'єднання).

    True — реально завантажив свіжу копію. False — об'єкта в бакеті ще
    нема (перший запуск) або мережева проблема; в обох випадках працюємо
    з тим, що є локально (sqlite3.connect створить нову базу за потреби).
    Помилки не кидаємо — персистентність best-effort, не має валити
    застосунок.
    """
    try:
        _client().download_file(config.R2_BUCKET, config.R2_OBJECT_KEY, str(local_path))
        return True
    except Exception as exc:
        logger.warning("R2 download skipped: %s", exc)
        return False


def upload_db(local_path: Path) -> None:
    """Заливає поточний cogs.db у R2. Викликається після кожного commit()
    у store.py. Помилки лише логуються — локальний запис вже відбувся."""
    try:
        _client().upload_file(str(local_path), config.R2_BUCKET, config.R2_OBJECT_KEY)
    except Exception as exc:
        logger.warning("R2 upload failed: %s", exc)
