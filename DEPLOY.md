# Деплой на Streamlit Community Cloud

Покрокова інструкція. Репозиторій — `reports/` (корінь), `walmartPayments/`
всередині нього — самодостатній пакет, який і деплоїться.

## 0. Передумови

- GitHub-акаунт (Streamlit Cloud деплоїть тільки з GitHub).
- Обліковка на [share.streamlit.io](https://share.streamlit.io) (вхід через
  GitHub).
- Опційно, але рекомендовано: акаунт Cloudflare (R2 — щоб `cogs.db`
  переживала редеплой, див. крок 3).

## 1. git remote і push

У `reports/` вже зроблено `git init`, `.gitignore` пускає в репозиторій
тільки `walmartPayments/`, `.streamlit/` і себе — жодні секрети, локальні
бази чи дані сусідніх проєктів у ньому не потраплять.

```bash
cd reports
git add -A
git status              # перевір: жодного .env, credentials/, *.db, *.csv/*.xlsx
git commit -m "walmartPayments: prepare for Streamlit Cloud deploy"
```

Створи приватний репозиторій на GitHub (порожній, без README/license —
вони вже є) і підключи:

```bash
git remote add origin https://github.com/<your-user>/<repo>.git
git branch -M main
git push -u origin main
```

**Приватний репозиторій обов'язково**, поки в ньому взагалі можуть
з'явитись чутливі дані — навіть з `.gitignore` краще не ризикувати.

## 2. Google service account (якщо ще нема)

Потрібен для експорту у Google Sheets. Якщо вже налаштований локально
(`walmartPayments/credentials/service_account.json` існує) — переходь до
кроку 3, JSON знадобиться там же для вставки в Secrets.

Якщо нема:

1. [Google Cloud Console](https://console.cloud.google.com) → новий проєкт
   (або існуючий) → APIs & Services → Credentials → Create Credentials →
   Service Account.
2. Створеному service account → Keys → Add Key → JSON. Завантажиться файл.
3. Відкрий Google-таблицю → Share → додай email service account
   (`...@....iam.gserviceaccount.com`) з роллю **Editor**.
4. Локально: поклади файл як `walmartPayments/credentials/service_account.json`
   (шлях фіксований, не перейменовувати).

## 3. Cloudflare R2 (опційно, але рекомендовано)

Без цього кроку `cogs.db` (ціни, історія запусків COGS) обнуляється при
кожному редеплої/рестарті — застосунок працюватиме, але дані не
персистентні. З R2 — переживають.

1. [Cloudflare dashboard](https://dash.cloudflare.com) → R2 Object Storage →
   Create bucket, назви, наприклад, `walmart-payments-cogs`.
2. R2 → Manage API tokens → Create API token → права **Object Read & Write**
   на цей бакет.
3. Запиши: Account ID (видно в R2 overview), Access Key ID, Secret Access
   Key, назву бакета — знадобляться в Secrets на кроці 5.

## 4. Створення застосунку на Streamlit Community Cloud

1. [share.streamlit.io](https://share.streamlit.io) → New app.
2. **Repository**: `<your-user>/<repo>` (той, що запушив на кроці 1).
3. **Branch**: `main`.
4. **Main file path**: `walmartPayments/webapp/Home.py`.
5. Advanced settings → **Python version**: `3.12` (обов'язково — pandas 3.0
   не збирається на старіших).
6. `walmartPayments/webapp/requirements.txt` підхопиться автоматично
   (лежить поруч з entrypoint-файлом) — нічого додатково вказувати не треба.
7. Deploy — перший білд може зайняти кілька хвилин.

## 5. Secrets

App settings → Secrets, вставити (значення — свої, з попередніх кроків):

```toml
WALMART_CLIENT_ID = "..."
WALMART_CLIENT_SECRET = "..."
WALMART_SELLER_ID = "..."
WALMART_PAYMENTS_GOOGLE_SHEET_ID = "..."
APP_PASSWORD = "..."          # обов'язково — без нього застосунок публічний

# Опційно — щоб cogs.db переживала редеплой (крок 3)
R2_ACCOUNT_ID = "..."
R2_ACCESS_KEY_ID = "..."
R2_SECRET_ACCESS_KEY = "..."
R2_BUCKET = "walmart-payments-cogs"

# Опційно — для експорту в Google Sheets (крок 2), вміст JSON-ключа
# один-в-один як TOML-таблиця
[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

`private_key` — переклопіюй з JSON-файлу як є, включно з `\n` всередині
рядка (TOML це коректно розпарсить). Save — застосунок перезапуститься
сам.

## 6. Перевірка після деплою

- [ ] Відкрити URL застосунку → з'являється екран **🔒 Вхід** (якщо задав
      `APP_PASSWORD`) → пароль приймається.
- [ ] Сторінка **Креди** → Client ID/Secret підтягнулись (не пусті).
- [ ] Сторінка **Ціни** → імпортувати тестовий xlsx (можна приклад з
      експандера) → з'являється в "Поточні ціни".
- [ ] Сторінка **Запуск** → обрати період → Walmart API повертає recon-файл
      → COGS рахується.
- [ ] Якщо налаштований R2 (крок 3): зроби "Reboot app" в Streamlit Cloud
      (імітація редеплою) → зайдені раніше ціни/історія запусків на місці.
- [ ] Google Sheets-експорт (якщо налаштований): дані з'являються в
      таблиці, форматування застосувалось.

## 7. Після деплою

- **Ротуй Walmart Client Secret.** Він певний час лежав зашитим у коді
  репозиторію — навіть після видалення з git-історії старе значення варто
  вважати скомпрометованим. Перевипустити в Walmart Seller Center →
  Developer → API keys, оновити в Secrets.
- Локальний `.env` і Cloud Secrets — незалежні копії кредів; зміна одного
  не зачіпає інший.

## Типові проблеми

| Симптом | Причина |
|---|---|
| `ModuleNotFoundError: No module named 'walmartPayments'` | Main file path вказаний неправильно, або `requirements.txt` не в тій директорії — має лежати поруч з `Home.py` (`walmartPayments/webapp/requirements.txt`) |
| Порожні Client ID/Secret на сторінці Креди | Secrets не збережені або назви ключів не збігаються з `WALMART_CLIENT_ID`/`WALMART_CLIENT_SECRET` |
| `Не знайдено service account JSON` при експорті в Sheets | Блок `[gcp_service_account]` відсутній у Secrets або невірно розпарсений TOML (перевір `private_key`) |
| Після Reboot ціни/історія зникли | R2 не налаштований (крок 3) або мережева помилка — дивись логи застосунку в Streamlit Cloud (Manage app → Logs), там `[r2_sync]`-попередження |
