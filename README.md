# walmartPayments

Автономне вивантаження **Walmart Payments** (recon / settlement) звітів через
Marketplace API — заміна ручному експорту з Seller Center.

Пакет самодостатній: не імпортує нічого з `adReport` чи інших частин репозиторію.
Ручний шлях не зачіпається.

---

## Швидкий старт

```bash
# які періоди виплат доступні
python -m walmartPayments.main list

# найсвіжіша виплата
python -m walmartPayments.main fetch --latest

# за розрахунковим періодом (сам знайде дату виплати)
python -m walmartPayments.main fetch --period 2026-07-11:2026-07-25

# за датою виплати напряму
python -m walmartPayments.main fetch --date 07282026

# тільки подивитись підсумки, нічого не зберігати
python -m walmartPayments.main fetch --latest --no-save

# завантажити і одразу зібрати PDF-виписку
python -m walmartPayments.main fetch --period 2026-07-11:2026-07-25 --pdf

# PDF з уже завантаженого файлу, без звернення до API
python -m walmartPayments.main pdf --file walmartPayments/reports/walmart_payments_2026-07-11_2026-07-25.csv

# те саме, але у Google Sheets замість/поряд з PDF
python -m walmartPayments.main fetch --period 2026-07-11:2026-07-25 --sheets
```

Запускати з кореня репозиторію (`C:\Users\Валерий\Desktop\reports`).

Файли лягають у `walmartPayments/reports/`:

| Файл | Що це |
|---|---|
| `walmart_payments_2026-07-11_2026-07-25.zip` | сира відповідь API |
| `walmart_payments_2026-07-11_2026-07-25.csv` | розпакований звіт, 43 колонки |
| `walmart_payments_2026-07-11_2026-07-25_summary.json` | агреговані підсумки |
| `walmart_payments_2026-07-11_2026-07-25.pdf` | виписка як у Seller Center (тільки з `--pdf`) |

---

## PDF-виписка

Дві сторінки:

1. **Виписка як у Seller Center** — шапка з періодом і `Paid to you`, секції
   Sales / Refunds / Services fees з підсумками, від'ємні суми червоним.
2. **Розкладка по товарах** — Item ID, одиниці, продажі, комісія, повернення,
   fees, нетто по кожному з товарів. Такого UI не показує взагалі.

Рядки, яких Recon API не віддає (`Shipping`, `WFS shipping reversal`,
`WFS shipping tax reversal`, `Opening balance`, `Reserves`, `Holds`),
друкуються як `$0.00` і позначені зірочкою з виноскою. Підсумки від цього не
страждають: у UI ці позиції взаємознищуються.

Одна видима відмінність від скріншота UI: `Net tax collected` показує `919.62`
замість `946.94`, бо UI друкує брутто і окремим рядком віднімає
`WFS shipping tax reversal −27.32`. У CSV одразу нетто. Різниця косметична,
`Total` секції збігається.

Потребує `reportlab` (імпортується лише під `--pdf`, решта CLI працює без нього)
і шрифти `C:/Windows/Fonts/arial.ttf`, `arialbd.ttf` — заради кирилиці.

---

## Google Sheets-експорт

`--sheets` пише те саме, що PDF, у дві вкладки спільної таблиці:
`"<період> Виписка"` (сторінка 1 PDF) і `"<період> Товари"` (сторінка 2).
Форматування — bold-заголовки, синій/сірий/червоний як у PDF, currency-формат
з автоматичним червоним для від'ємних сум, заморожений header row у таблиці
товарів. Повторний запуск для того самого періоду перезаписує вкладки, не
плодить дублікати.

Потребує `gspread` + `gspread-formatting` (імпортуються лише під `--sheets`)
і service account:

1. Google Cloud Console → створити проєкт (або взяти наявний) → увімкнути
   **Google Sheets API** → **IAM & Admin → Service Accounts** → створити
   service account → **Keys → Add key → JSON**, зберегти файл.
2. Покласти цей JSON саме як `walmartPayments/credentials/service_account.json`
   — шлях фіксований у коді (не env var, не налаштовується), щоб однаково
   працювало локально і на деплої без абсолютних шляхів з іншої машини.
3. Створити порожню Google-таблицю вручну, скопіювати її ID з URL
   (`https://docs.google.com/spreadsheets/d/<ID>/edit`).
4. Розшарити цю таблицю на email service account'а
   (`...@...iam.gserviceaccount.com` з JSON-ключа), роль **Editor**.
5. Виставити `WALMART_PAYMENTS_GOOGLE_SHEET_ID` (див. `Конфігурація` нижче).

---

## COGS (собівартість)

Ціни зберігаються версійовано в SQLite (`cogs/cogs.db`, шлях налаштовується
через `WALMART_PAYMENTS_COGS_DB`): кожна ціна має `effective_from`, і при
розрахунку COGS для періоду береться ціна, що діяла станом на кінець цього
періоду — **не поточна**. Тому зміна ціни (наприклад, через пів року) не
чіпає вже пораховані минулі періоди. Кожен запуск розрахунку пишеться
незмінним рядком в історію (`cogs_runs`/`cogs_run_items`) — повторний
розрахунок того самого періоду створює новий запис, а не перезаписує старий.

Ключ товару — `SKU` (те саме значення, що `Partner Item Id` у recon CSV;
перевірено на реальному звіті — це саме SKU, а не числовий Walmart Item ID).

### Імпорт прайсу

Прайс-файл — xlsx з аркушем `COGS`, колонки `Product`, `amazon ASIN`, `SKU`,
`Item ID`, `Total price/unit` (такий формат лежить у корені пакета —
`Walmart - Payments.xlsx`).

```bash
python -m walmartPayments.main cogs import-prices --file "walmartPayments/Walmart - Payments.xlsx"

# з датою дії нової ціни (дефолт — сьогодні; для нового SKU завжди 1900-01-01)
python -m walmartPayments.main cogs import-prices --file prices.xlsx --effective-from 2026-08-10
```

Ціна для SKU, якого раніше не було в базі, автоматично діє "завжди"
(`1900-01-01`), щоб покривати вже завантажені історичні звіти. Для SKU з
уже відомою ціною новий запис діє з переданої (або сьогоднішньої) дати, а
стара версія лишається чинною для минулих дат.

### Розрахунок

```bash
python -m walmartPayments.main cogs compute --file walmartPayments/reports/walmart_payments_2026-07-11_2026-07-25.csv

# ціни станом на конкретну дату замість кінця періоду
python -m walmartPayments.main cogs compute --file ... --as-of 2026-07-20
```

Юніти й виручка рахуються з рядків `Sale`/`Refund` з `Amount Type == Product
Price` (юніти повернень віднімаються — нетто). SKU без ціни на обрану дату
позначаються окремо, а не тихо йдуть у COGS = 0.

### COGS у Google Sheets

Вкладка `"<період> Виписка"` дописується трьома рядками в кінці: `Total
COGS`, `Advertising (Spend)`, `TACOS %`. `Advertising (Spend)` — вводиться
вручну АБО рахується автоматично з CSV-експорту Walmart Ads "PPC Item
Performance" (сума колонки `Ad Spend`; файл не містить дати, тому
вважається, що покриває той самий період, що й завантажений recon-звіт;
завантажується через форму на сторінці, назва файлу довільна). Значення
зберігається в SQLite (таблиця
`period_extras`, ключ — період) і підставляється як дефолт при наступному
відкритті того самого періоду. `TACOS % = Advertising Spend / Виручка
(нетто) × 100`. Ці три рядки пише лише сторінка ▶️ Запуск веб-інтерфейсу
(кнопка "Експортувати у Google Sheets") — `--sheets` у CLI їх не знає, бо
там немає ні розрахованого COGS, ні місця ввести рекламні витрати.

---

## Веб-інтерфейс (Streamlit)

```bash
streamlit run walmartPayments/webapp/Home.py
```

Сторінки (у сайдбарі):

| Сторінка | Що робить |
|---|---|
| 🔑 Креди | Walmart API / Google Sheets credentials — зберігаються в `walmartPayments/.env`, не в коді |
| 💲 Ціни | імпорт прайсу з xlsx (з попереднім переглядом змін), ручне додавання/оновлення ціни, історія цін по SKU |
| ▶️ Запуск | обрати розрахунковий період (система сама знайде дату виплати через Walmart API) і дату, на яку брати ціни, порахувати COGS, ввести Advertising Spend і вивантажити все у Google Sheets |
| 📜 Історія | усі попередні запуски розрахунку COGS — незмінні знімки, включно з цінами, які фактично використались |

Сторінка "Запуск" завжди звертається до Walmart API за розрахунковим
періодом (rate limit жорсткий, див. нижче).

---

## Конфігурація

`config.py` читає тільки environment variables — зашитих у код значень
немає (секрети в git не потрапляють). `.env` поруч з пакетом (куди пише
сторінка "Креди") підхоплюється автоматично при старті:

| Змінна | Призначення |
|---|---|
| `WALMART_CLIENT_ID` | Marketplace client id |
| `WALMART_CLIENT_SECRET` | Marketplace client secret |
| `WALMART_SELLER_ID` | Seller id (йде в заголовок `WM_SELLER_ID`) |
| `WALMART_PAYMENTS_OUTPUT_DIR` | куди складати файли |
| `WALMART_PAYMENTS_COGS_DB` | шлях до SQLite з цінами і історією COGS (дефолт `cogs/cogs.db`) |
| `WALMART_PAYMENTS_GOOGLE_SHEET_ID` | ID Google-таблиці, розшареної на service account (для `--sheets`) |

Service account JSON шляху як env var не має — фіксований
`walmartPayments/credentials/service_account.json`, кладеться туди напряму.

---

## Деплой на Streamlit Community Cloud

Покрокова інструкція (git remote, R2, Secrets, чеклист перевірки) — у
[**DEPLOY.md**](DEPLOY.md).

---

## API, який використовується

```
GET /v3/report/reconreport/availableReconFiles?reportVersion=v1
    Accept: application/json
    -> {"availableApReportDates": ["07282026", "07142026", ...]}

GET /v3/report/reconreport/reconFile?reportDate=07282026&reportVersion=v1
    Accept: application/octet-stream
    -> ZIP-байти
```

Обов'язкові заголовки на обох: `WM_SEC.ACCESS_TOKEN`, `WM_SVC.NAME`,
`WM_QOS.CORRELATION_ID`, `WM_SELLER_ID`, `WM_CONSUMER.CHANNEL.TYPE`.

### Підводні камені (перевірено емпірично)

1. **`Accept` для `reconFile` має бути `application/octet-stream`.**
   `application/json` дає HTTP 200, але тіло —
   `{"status":"ERROR","message":"No acceptable representation"}`.
   `text/csv` і `*/*` дають 406 з порожнім тілом.
   `api.py` тому ще й перевіряє, що відповідь починається з `PK`.

2. **Рядок 1 CSV — сміттєвий трейлер** `Number of Lines in file 5280`.
   Без його відкидання `csv.DictReader` створює фальшивий рядок даних.

3. **Період і сума виплати лежать в окремому рядку** з
   `Transaction Type = PaymentSummary`. У транзакційних рядках колонки
   `Period Start Date` / `Period End Date` / `Total Payable` порожні.

4. **Rate limit жорсткий** — 429 прилітає вже після кількох запитів підряд.
   `api.py` тримає паузу 45с між викликами і 15 хв після реального 429.
   Не знижуй `MIN_REQUEST_INTERVAL_SECONDS`.

5. **Дата виплати не дорівнює кінцю періоду.** Файл `07282026` покриває
   `07/11/2026 - 07/25/2026`. `--period` резолвиться у найближчу доступну
   дату виплати, не раніше кінця періоду; якщо період ще не виплачений —
   помилка, а не тихий чужий файл.

---

## Формат звіту

CSV має 43 колонки. Ключові:

| Колонка | Приклад |
|---|---|
| `Period Start Date` / `Period End Date` | `07/11/2026` / `07/25/2026` |
| `Total Payable` / `Currency` | `8805.63` / `USD` |
| `Transaction Type` | `Sale`, `Refund`, `Adjustment`, `Service Fee`, `PaymentSummary` |
| `Amount` / `Amount Type` | `16.99` / `Product Price` |
| `Customer Order #`, `Purchase Order #` | ідентифікатори замовлення |
| `Partner Item Id`, `Partner GTIN`, `Partner Item Name` | товар |
| `Commission Rate`, `Base Commission Rate` | комісія |
| `Ship to State` / `City` / `Zipcode` | доставка |
| `Fulfillment Type`, `Campaign Id`, `Store Id` | інше |

`Amount Type`, що зустрічаються: `Product Price`, `Commission on Product`,
`Product tax`, `Product tax withheld`, `Other tax (Fees)`, `Promo Code`,
`Total Walmart Funded Savings`, `Fee/Reimbursement`,
`WFS Inventory Fee/Reimbursement`.

**Дані transaction-level**, а не лише підсумки — можна розкласти виплату по
товарах і замовленнях, чого UI не дає.

---

## Звірка з Seller Center

Період `2026-07-11 .. 2026-07-25`, реальний прогін:

| Секція | UI | walmartPayments |
|---|---:|---:|
| Sales Total | $14,072.93 | 14,072.93 |
| Refunds Total | −$195.43 | −195.43 |
| Services fees | −$5,071.87 | −5,071.87 |
| **Paid to you** | **$8,805.63** | **8,805.63** |

Розбіжність `0.00`.

Одна пастка при порівнянні окремих рядків: у UI **Product price** уже нетто
від промо (`16,558.04 − 5.00 = 16,553.04`), а в CSV це два різні `Amount Type`
(`Product Price` і `Promo Code`).

`Opening balance`, `Reserves`, `Holds` у recon-файл не потрапляють — вони є
лише в UI. На цьому періоді всі три нульові.

---

## Структура

```
walmartPayments/
├── __init__.py     публічне API пакета
├── config.py       креди, endpoints, тайминги
├── api.py          HTTP-клієнт: токен, тротлінг, ретраї, завантаження ZIP
├── parser.py       ZIP -> CSV -> ReconReport (трейлер, PaymentSummary)
├── summary.py      агрегація по (Transaction Type, Amount Type)
├── pdf_report.py   PDF-виписка (reportlab, лише під --pdf)
├── sheets_report.py  той самий вигляд у Google Sheets (gspread, лише під --sheets)
├── main.py         CLI
└── reports/        вивантажені файли
```

Агрегація в `summary.py` навмисно без whitelist: новий тип транзакції від
Walmart з'явиться в розбивці окремим рядком, а не зникне тихо.
