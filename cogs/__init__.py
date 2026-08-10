# -*- coding: utf-8 -*-
"""cogs — розрахунок собівартості (COGS) з версіонованими цінами.

Ціни зберігаються з датою дії (effective_from), тому зміна ціни не чіпає
розрахунки за минулі періоди: при розрахунку COGS береться ціна, що діяла
станом на дату періоду (за замовчуванням period_end), а не поточна.
Кожен запуск розрахунку пишеться в історію (cogs_runs/cogs_run_items) і
більше не змінюється — повторний запуск створює новий запис, а не
перезаписує старий.
"""

from .calculator import compute_cogs
from .price_import import import_from_xlsx
from .store import connect

__all__ = ["compute_cogs", "import_from_xlsx", "connect"]
