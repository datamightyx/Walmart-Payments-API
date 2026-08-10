# -*- coding: utf-8 -*-
"""Сторінка Streamlit: перегляд і збереження credentials у .env."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values, set_key

from _common import config, page_header, reload_config

import streamlit as st

page_header("Credentials", "🔑")
st.caption(f"Walmart / Google Sheets / R2 — зберігаються у {config.ENV_FILE} (не в коді).")

env_values = dotenv_values(config.ENV_FILE) if config.ENV_FILE.exists() else {}


def _source(key: str) -> str:
    return ".env" if key in env_values else "дефолт у коді / env змінна"


def _status_row(label: str, ok: bool, ok_text: str, missing_text: str) -> None:
    icon = "✅" if ok else "⚠️"
    text = ok_text if ok else missing_text
    st.markdown(f"{icon} **{label}** — {text}")


# ── Огляд стану ───────────────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("Стан", anchor=False)
    col1, col2 = st.columns(2)
    with col1:
        _status_row(
            "Walmart API", bool(config.CLIENT_ID and config.CLIENT_SECRET),
            "креди задані", "Client ID / Secret порожні",
        )
        _status_row(
            "Google Sheets ID", bool(config.GOOGLE_SHEET_ID),
            "задано", "не задано — --sheets / експорт недоступні",
        )
        _status_row(
            "Service account", config.GOOGLE_CREDENTIALS_FILE.exists(),
            "ключ знайдено", "JSON-ключ не знайдено",
        )
    with col2:
        _status_row(
            "Cloudflare R2", bool(config.R2_ACCOUNT_ID and config.R2_BUCKET),
            f"бакет {config.R2_BUCKET!r}", "не налаштовано — cogs.db не переживе редеплой",
        )
        try:
            has_app_password = bool(st.secrets.get("APP_PASSWORD"))
        except Exception:
            has_app_password = False
        _status_row(
            "Password-gate", has_app_password,
            "увімкнено", "вимкнено — застосунок публічний за URL",
        )

st.divider()

# ── Редагування ───────────────────────────────────────────────────────────
if "show_secret" not in st.session_state:
    st.session_state.show_secret = False

btn_col, _ = st.columns([1, 3])
with btn_col:
    if st.button(
        "🙈 Приховати" if st.session_state.show_secret else "👁 Показати повністю",
        width="stretch",
    ):
        st.session_state.show_secret = not st.session_state.show_secret
        st.rerun()

with st.form("creds_form", border=True):
    st.subheader("Walmart Marketplace API", anchor=False)
    client_id = st.text_input(
        "Client ID", value=config.CLIENT_ID,
        help=f"Джерело: {_source('WALMART_CLIENT_ID')}",
    )
    client_secret = st.text_input(
        "Client Secret", value=config.CLIENT_SECRET,
        type="default" if st.session_state.show_secret else "password",
        help=f"Джерело: {_source('WALMART_CLIENT_SECRET')}",
    )
    seller_id = st.text_input(
        "Seller ID", value=config.SELLER_ID,
        help=f"Джерело: {_source('WALMART_SELLER_ID')}",
    )

    st.subheader("Google Sheets", anchor=False)
    st.caption("Опційно — потрібен для експорту COGS-виписки у Sheets.")
    sheet_id = st.text_input(
        "Google Sheet ID", value=config.GOOGLE_SHEET_ID,
        help=f"Джерело: {_source('WALMART_PAYMENTS_GOOGLE_SHEET_ID')}",
    )

    submitted = st.form_submit_button("💾 Зберегти", type="primary", width="stretch")

if submitted:
    updates = {
        "WALMART_CLIENT_ID": client_id.strip(),
        "WALMART_CLIENT_SECRET": client_secret.strip(),
        "WALMART_SELLER_ID": seller_id.strip(),
        "WALMART_PAYMENTS_GOOGLE_SHEET_ID": sheet_id.strip(),
    }

    if not config.ENV_FILE.exists():
        config.ENV_FILE.touch()
    for key, value in updates.items():
        if value:
            set_key(str(config.ENV_FILE), key, value)

    reload_config()
    st.success("Збережено.")
    st.rerun()

# ── Service account JSON ─────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("Service account JSON", anchor=False)
    if config.GOOGLE_CREDENTIALS_FILE.exists():
        st.success(f"✅ Знайдено: `{config.GOOGLE_CREDENTIALS_FILE}`")
    else:
        st.warning(
            f"⚠️ Не знайдено. Поклади JSON-ключ service account саме сюди "
            f"(шлях фіксований, не налаштовується — щоб працювало однаково "
            f"локально і на деплої):\n\n`{config.GOOGLE_CREDENTIALS_FILE}`\n\n"
            f"На Streamlit Cloud — через блок `[gcp_service_account]` у Secrets "
            f"(записується автоматично при старті, див. DEPLOY.md)."
        )
