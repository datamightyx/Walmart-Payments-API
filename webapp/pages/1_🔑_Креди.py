# -*- coding: utf-8 -*-
"""Сторінка Streamlit: перегляд і збереження credentials у .env."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values, set_key

from _common import config, page_header, reload_config

import streamlit as st

page_header("Креди", "🔑")

st.caption(f"Зберігаються у {config.ENV_FILE} (не в коді).")

env_values = dotenv_values(config.ENV_FILE) if config.ENV_FILE.exists() else {}


def _source(key: str) -> str:
    return ".env" if key in env_values else "дефолт у коді / env змінна"


if "show_secret" not in st.session_state:
    st.session_state.show_secret = False

if st.button("🙈 Приховати" if st.session_state.show_secret else "👁 Показати повністю"):
    st.session_state.show_secret = not st.session_state.show_secret
    st.rerun()

with st.form("creds_form"):
    st.subheader("Walmart Marketplace API")
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

    st.subheader("Google Sheets (опційно, для --sheets)")
    sheet_id = st.text_input(
        "Google Sheet ID", value=config.GOOGLE_SHEET_ID,
        help=f"Джерело: {_source('WALMART_PAYMENTS_GOOGLE_SHEET_ID')}",
    )

    submitted = st.form_submit_button("Зберегти", type="primary")

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

st.divider()
st.subheader("Service account JSON")
if config.GOOGLE_CREDENTIALS_FILE.exists():
    st.success(f"✅ Знайдено: {config.GOOGLE_CREDENTIALS_FILE}")
else:
    st.warning(
        f"⚠️ Не знайдено. Поклади JSON-ключ service account саме сюди "
        f"(шлях фіксований, не налаштовується — щоб працювало однаково "
        f"локально і на деплої):\n\n`{config.GOOGLE_CREDENTIALS_FILE}`"
    )
