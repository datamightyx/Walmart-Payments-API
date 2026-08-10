# -*- coding: utf-8 -*-
"""HTTP-клієнт Walmart Marketplace Recon (Payments / Settlement) API.

Два endpoint-и:
    GET /v3/report/reconreport/availableReconFiles?reportVersion=v1
        -> {"availableApReportDates": ["07282026", "07142026", ...]}
    GET /v3/report/reconreport/reconFile?reportDate=07282026&reportVersion=v1
        -> ZIP-байти з одним CSV усередині

Обидва перевірені на живому акаунті.
"""

from __future__ import annotations

import base64
import logging
import time
import uuid
from typing import Callable

import requests

import config

logger = logging.getLogger(__name__)


class WalmartAPIError(RuntimeError):
    """Помилка виклику Walmart API."""


class WalmartPaymentsAPI:
    """Клієнт із вбудованим тротлінгом і кешуванням токена.

    Тротлінг обов'язковий: Walmart віддає 429 вже після кількох запитів
    підряд, а після 429 потрібна довга пауза (~15 хв), а не миттєвий ретрай.
    """

    # Коди, які мають сенс ретраїти. 401/403 включені навмисно: gateway
    # періодично віддає UNAUTHORIZED навіть на валідних кредах.
    RETRYABLE_STATUSES = frozenset({401, 403, 429, 500, 502, 503, 504})

    def __init__(
        self,
        client_id: str = config.CLIENT_ID,
        client_secret: str = config.CLIENT_SECRET,
        seller_id: str = config.SELLER_ID,
        min_request_interval: float = config.MIN_REQUEST_INTERVAL_SECONDS,
        throttle_cooldown: float = config.THROTTLE_COOLDOWN_SECONDS,
        max_retries: int = config.MAX_RETRIES,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.seller_id = seller_id
        self.min_request_interval = min_request_interval
        self.throttle_cooldown = throttle_cooldown
        self.max_retries = max_retries

        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._last_request_at: float = 0.0

        # Викликач прогресу (напр. Streamlit-статус). Не параметр __init__,
        # бо клієнт кешується як singleton (st.cache_resource) — виставляється
        # на інстанс перед кожним запуском, а не при створенні.
        self.on_progress: Callable[[str], None] | None = None

    def _report(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message)

    # ── низькорівневе ────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        """М'яка пауза між будь-якими двома запитами."""
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_request_interval - elapsed
        if self._last_request_at and wait > 0:
            logger.info("Пауза %.0fс перед наступним запитом (rate limit)", wait)
            self._report(f"Пауза {wait:.0f}с перед наступним запитом (rate limit)…")
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _access_token(self) -> str:
        """Повертає токен, оновлюючи його лише коли термін майже вийшов."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        self._report("Отримання токена доступу…")
        auth = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        resp = self._session.post(
            f"{config.BASE_URL}/v3/token",
            headers={
                "Authorization": f"Basic {auth}",
                "WM_SVC.NAME": "Walmart Marketplace",
                "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            raise WalmartAPIError(
                f"Не вдалось отримати токен: {resp.status_code} {resp.text[:500]}"
            )

        payload = resp.json()
        self._token = payload["access_token"]
        ttl = int(payload.get("expires_in", config.TOKEN_TTL_SECONDS))
        self._token_expires_at = (
            time.monotonic() + ttl - config.TOKEN_REFRESH_MARGIN_SECONDS
        )
        return self._token

    def _headers(self, accept: str) -> dict:
        return {
            "WM_SEC.ACCESS_TOKEN": self._access_token(),
            "WM_SVC.NAME": "Walmart Marketplace",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            "WM_SELLER_ID": self.seller_id,
            "WM_CONSUMER.CHANNEL.TYPE": self.client_id,
            "Accept": accept,
        }

    def _get(self, path: str, params: dict, accept: str) -> requests.Response:
        """GET із тротлінгом і довгим backoff на 429."""
        last_error = ""

        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            resp = self._session.get(
                f"{config.BASE_URL}{path}",
                headers=self._headers(accept),
                params=params,
                timeout=config.HTTP_TIMEOUT_SECONDS,
            )

            if resp.status_code == 200:
                return resp

            last_error = f"{resp.status_code} {resp.text[:500]}"

            if resp.status_code not in self.RETRYABLE_STATUSES:
                raise WalmartAPIError(f"GET {path} -> {last_error}")

            if attempt == self.max_retries:
                break

            # 429 -> довга пауза; інші ретраєбл коди -> коротка.
            cooldown = (
                self.throttle_cooldown
                if resp.status_code == 429
                else self.min_request_interval
            )
            logger.warning(
                "GET %s -> %s. Спроба %s/%s, пауза %.0fс",
                path, resp.status_code, attempt, self.max_retries, cooldown,
            )
            self._report(
                f"{resp.status_code} від Walmart, спроба {attempt}/{self.max_retries}, "
                f"пауза {cooldown:.0f}с…"
            )
            time.sleep(cooldown)
            # Після паузи токен міг протухнути — форсуємо оновлення.
            self._token = None

        raise WalmartAPIError(
            f"GET {path} не вдався після {self.max_retries} спроб: {last_error}"
        )

    # ── публічне API ─────────────────────────────────────────────────────────

    def available_payout_dates(self) -> list[str]:
        """Дати виплат у форматі MMDDYYYY, відсортовані від нових до старих."""
        self._report("Запитування доступних дат виплат…")
        resp = self._get(
            "/v3/report/reconreport/availableReconFiles",
            {"reportVersion": config.REPORT_VERSION},
            accept="application/json",
        )
        dates = resp.json().get("availableApReportDates", [])
        # API віддає їх у довільному порядку; сортуємо як YYYYMMDD.
        return sorted(dates, key=lambda d: d[4:] + d[:4], reverse=True)

    def download_recon_zip(self, payout_date: str) -> bytes:
        """Сирі ZIP-байти recon-звіту за дату виплати (MMDDYYYY)."""
        self._report(f"Завантаження recon-файлу за {payout_date}…")
        resp = self._get(
            "/v3/report/reconreport/reconFile",
            {"reportDate": payout_date, "reportVersion": config.REPORT_VERSION},
            accept=config.RECON_ACCEPT,
        )

        # Захист від "успішної" відповіді з тілом-помилкою: при невірному
        # Accept Walmart віддає 200 + {"status":"ERROR", ...} замість ZIP.
        if not resp.content.startswith(b"PK"):
            raise WalmartAPIError(
                f"Очікувався ZIP, прийшло {resp.headers.get('Content-Type')}: "
                f"{resp.content[:300]!r}"
            )
        return resp.content
