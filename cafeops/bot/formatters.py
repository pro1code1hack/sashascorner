"""Каждая строка, которую видит человек. Русский язык живёт только здесь.

THE ONLY PLACE IN THE CODEBASE THAT HOLDS RUSSIAN. Agent brief, and
`ARCHITECTURE.md` 0 (spec 13.6): `domain/` is locale-free -- `domain/units.format_qty`
renders `L`/`ml`/`kg`/`pcs` for back-office output on purpose -- and the web dashboard is
English only, confirmed with the owner. The bot is the one Russian surface, so it gets
one Russian module and nothing outside it may contain a user-facing string. That
includes button labels, which is why `BTN_*` live here and `keyboards.py` imports them.

Four invariants are kept by the wording itself, not by the data:

* **Invariant 6** -- every quantity is printed with its basis. `расчётный остаток` never
  appears without either `опора: пересчёт ...` or the warning that there is none. A
  figure with no count behind it is a bare movement sum and the message says so.
* **Invariant 9** -- `_low_confidence` returns a sentence that goes INSTEAD OF the
  forecast. There is deliberately no formatter that prints a quantity next to it.
* **Invariant 4** -- `_cap` names the cap and says outright that raising the line
  recreates the waste the cap prevented. A cap the owner does not understand is a cap
  she overrides.
* **The top-up** -- `_order_top_ups` names every line that exists only to clear a
  minimum. "She must never discover she bought syrup to clear a £50 floor."

Plain text, no parse mode. Ingredient names carry brackets and percent signs
(`Coffee beans (house blend)`, `Milk 3.5%`), and an escaping mistake in a Markdown
build is a message that fails to send at 07:00 rather than one that looks wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from cafeops.bot.viewmodels import (
    BasketLineView,
    BasketView,
    CapKind,
    CapNotice,
    CashDayView,
    ChecklistItemView,
    CountItemView,
    CountResultView,
    CountSessionKind,
    CsvKind,
    DeliveryLineView,
    DeliveryOrderView,
    DigestView,
    DispatchView,
    DriftAlertView,
    EmergencyDigestView,
    ExpiryLineView,
    ExportView,
    ImportResultView,
    IngredientRefView,
    LowConfidenceKind,
    LowConfidenceNotice,
    MenuPageView,
    MenuPickView,
    OrderLineView,
    OrderView,
    ReceiptIssue,
    ReceiptView,
    RecordedSaleView,
    RevocationView,
    StockLineView,
    WriteOffView,
)
from cafeops.config import settings
from cafeops.domain.drift import DriftCause
from cafeops.domain.enums import BrowserJobStatus
from cafeops.domain.tiers import GateAction
from cafeops.domain.types import (
    DriftVerdict,
    GateAlertLevel,
    OrderChannel,
    OrderNoteKind,
    POStatus,
    RevokeCause,
    SaleChannel,
    Storage,
    Tier,
    Unit,
)
from cafeops.services.record_checklist import ChecklistOrderRequest

if TYPE_CHECKING:
    from cafeops.services.browser_jobs import BasketJobNotice

    # Type-only: the loyalty services import the bot's notifier, so a runtime import
    # here would make `formatters` and `services.loyalty` import each other.
    from cafeops.services.loyalty.admin import MemberBrief
    from cafeops.services.loyalty.alerts import AlertView
    from cafeops.services.loyalty.stats import DailySummary

__all__ = [
    "BTN_CHECKLIST_LOW",
    "BTN_CHECKLIST_NO_ORDER",
    "BTN_CHECKLIST_OK",
    "BTN_CHECKLIST_SKIP",
    "BTN_COUNT_SKIP",
    "BTN_COUNT_STOP",
    "BTN_DELIVERY_NO_DATE",
    "BTN_DELIVERY_SKIP",
    "BTN_ORDER_CANCEL",
    "BTN_ORDER_CONFIRM",
    "BTN_ORDER_MINUS",
    "BTN_ORDER_PLUS",
    "adhoc_expiry_prompt",
    "adhoc_not_found",
    "adhoc_qty_prompt",
    "adhoc_usage",
    "alerts",
    "checklist_done",
    "checklist_intro",
    "checklist_order_prompt",
    "checklist_order_refused",
    "checklist_ordered",
    "checklist_prompt",
    "checklist_result",
    "count_done",
    "count_intro",
    "count_nothing_due",
    "count_prompt",
    "count_result",
    "daily_summary",
    "delivery_expiry_prompt",
    "delivery_intro",
    "delivery_nothing_expected",
    "delivery_qty_prompt",
    "delivery_receipt",
    "digest",
    "err_bad_date",
    "err_bad_number",
    "err_bad_packs",
    "err_fractional_count",
    "err_not_adjustable",
    "err_order_all_zero",
    "err_unknown",
    "help_text",
    "job_drift_report",
    "job_new_drafts",
    "job_nothing_to_report",
    "member_brief",
    "member_not_found",
    "member_usage",
    "money",
    "order_card",
    "order_confirmed",
    "order_dispatched",
    "order_nothing_to_confirm",
    "start_text",
]


# ==========================================================================
# Числа, единицы, даты
# ==========================================================================

#: Единицы в том виде, в каком их читает человек за прилавком. `domain/units` даёт
#: `L`/`ml`/`kg`/`pcs` для служебного вывода и остаётся без локали.
_UNITS: dict[Unit, str] = {
    Unit.L: "л",
    Unit.ML: "мл",
    Unit.KG: "кг",
    Unit.G: "г",
    Unit.EACH: "шт",
}

_STORAGE: dict[Storage, str] = {
    Storage.AMBIENT: "склад",
    Storage.CHILLED: "холодильник",
    Storage.FROZEN: "морозильник",
}

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_TIERS: dict[Tier, str] = {Tier.A: "A", Tier.B: "B", Tier.C: "C"}


def _runs(n: int) -> str:
    return f"{n} {_plural(n, 'поход', 'похода', 'походов')}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование: 1 день, 2 дня, 5 дней, 21 день."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _days(n: int) -> str:
    return f"{n} {_plural(n, 'день', 'дня', 'дней')}"


def _items(n: int) -> str:
    return f"{n} {_plural(n, 'позиция', 'позиции', 'позиций')}"


def _lines_word(n: int) -> str:
    return f"{n} {_plural(n, 'строка', 'строки', 'строк')}"


def _packs(n: int) -> str:
    return f"{n} {_plural(n, 'упаковка', 'упаковки', 'упаковок')}"


def _num(qty: Decimal, unit: Unit) -> str:
    """Количество без единицы. Счётное -- целым, объём и вес -- как в журнале.

    Счётное округляется до целого, и это безопасно только потому, что дробный
    ПЕРЕСЧЁТ по счётной позиции бот не принимает (см. `err_fractional_count`). Иначе
    округление врало бы человеку о том, что записано в журнал: расчётный остаток
    вполне бывает дробным -- 106.534 стакана -- потому что к расходу применён
    коэффициент потерь, и вот его округлить можно, это оценка. Записанное число --
    нельзя.
    """
    if unit is Unit.EACH:
        return f"{qty.quantize(Decimal('1'))}"
    if unit in (Unit.ML, Unit.G):
        return f"{qty.quantize(Decimal('0.1')):f}"
    return f"{qty.quantize(Decimal('0.001')):f}"


def _qty(qty: Decimal, unit: Unit) -> str:
    return f"{_num(qty, unit)} {_UNITS[unit]}"


def money(pence: int | Decimal | None) -> str:
    """`£12.34` -- the bot's one money format (keyboards import it too).

    Not `domain/units.pounds`: that writes a negative as `-£1.50` and rounds a Decimal to
    whole pence first; this writes `£-1.50`. Different output, so not a copy (8X).
    """
    if pence is None:
        return "цена неизвестна"
    return f"£{Decimal(pence) / 100:.2f}"


def _tz() -> ZoneInfo:
    return settings.tz


def _d(value: date | datetime) -> str:
    if isinstance(value, datetime):
        value = value.astimezone(_tz()).date()
    return f"{value:%d.%m}"


def _dt(value: datetime) -> str:
    return f"{value.astimezone(_tz()):%d.%m %H:%M}"


def _weekdays(iso: Sequence[int]) -> str:
    if not iso:
        return "любой день (самовывоз)"
    return "/".join(_WEEKDAYS[day - 1] for day in sorted(iso))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}%"


# ==========================================================================
# Инвариант 6: у каждого числа есть основание
# ==========================================================================


def _basis(line: StockLineView) -> str:
    """Откуда взялось число. Без этой строки цифру показывать нельзя."""
    if not line.has_count_basis:
        return (
            "БЕЗ ПЕРЕСЧЁТА: под этим числом нет ни одного физического пересчёта, "
            f"это просто сумма движений по журналу ({line.movement_count}). "
            "Для заказа на такую цифру опираться нельзя — сначала пересчитать."
        )
    counted = line.basis_count_qty
    unit = line.unit
    ago = ""
    if line.days_since_count == 0:
        ago = ", сегодня"
    elif line.days_since_count is not None:
        ago = f", {_days(line.days_since_count)} назад"
    figure = "—" if counted is None else _qty(counted, unit)
    # `has_count_basis` implies a count date, but the view model carries them as two
    # fields, so the type does not know that; the dash is the same one `figure` uses.
    when = "—" if line.basis_counted_at is None else _d(line.basis_counted_at)
    return f"опора: пересчёт {figure} от {when}{ago} плюс {line.movement_count} движ. по журналу"


def _stock_figure(line: StockLineView) -> str:
    """Расчётный остаток с пометкой. `is_theoretical` всегда True (инвариант 6)."""
    head = f"расчётный остаток {_qty(line.qty, line.unit)}"
    if not line.is_theoretical:  # pragma: no cover - OnHand.is_theoretical всегда True
        head = f"остаток {_qty(line.qty, line.unit)}"
    return head


def _stock_row(line: StockLineView) -> list[str]:
    rows = [f"• {line.name} [{_TIERS[line.tier]}] — {_stock_figure(line)}", f"    {_basis(line)}"]
    if line.qty < 0:
        rows.append(
            "    ОТРИЦАТЕЛЬНЫЙ остаток: по журналу списано больше, чем было по пересчёту. "
            "Это не «ноль», это расхождение — пересчитать."
        )
    if line.soonest_expiry_days is not None and line.soonest_expiry_days <= 3:
        rows.append(
            f"    ближайшая партия: {_days(line.soonest_expiry_days)} до конца срока"
            if line.soonest_expiry_days >= 0
            else f"    ближайшая партия просрочена на {_days(-line.soonest_expiry_days)}"
        )
    return rows


# ==========================================================================
# Инвариант 9: причина ВМЕСТО числа
# ==========================================================================


def _low_confidence(notice: LowConfidenceNotice) -> str:
    """Сообщение, которое СТАНОВИТСЯ НА МЕСТО прогноза, а не рядом с ним.

    Отдельной функции «показать прогноз и рядом предупреждение» здесь нет и не будет:
    инвариант 9 — это «сказать вместо числа», и как только цифра снова оказывается на
    экране, предупреждение перестаёт работать.
    """
    if notice.kind is LowConfidenceKind.NO_HISTORY:
        window = "" if notice.needed_days is None else f" за последние {_days(notice.needed_days)}"
        return (
            f"ПРОГНОЗ НЕ ПОКАЗАН: расход{window} не зафиксирован вообще. "
            "Ноль здесь означает «ничего не известно», а не «ничего не нужно»."
        )
    if notice.kind is LowConfidenceKind.THIN_HISTORY:
        have = "" if notice.history_days is None else f"всего {_days(notice.history_days)}"
        need = "" if notice.needed_days is None else f", нужно {_days(notice.needed_days)}"
        return (
            f"ПРОГНОЗ НЕ ПОКАЗАН: истории {have}{need}. "
            "Вместо прогноза взято плоское среднее без поправки на день недели — "
            "количество ниже считайте заглушкой, а не прогнозом."
        )
    if notice.kind is LowConfidenceKind.NO_PRIOR_SEASON:
        return (
            "ПРОГНОЗ НЕ ПОКАЗАН: этот сезон идёт впервые, сравнивать не с чем. "
            "Количество взято по плоской ставке первых дней сезона, и первые недели "
            "сезона — самые непоказательные. Это заглушка, а не прогноз."
        )
    if notice.kind is LowConfidenceKind.UNTRUSTWORTHY_STOCK:
        return (
            "ПРОГНОЗ НЕ ПОКАЗАН: расхождение по этой позиции выше допустимого, значит "
            "остаток, из которого вычитается прогноз, сам недостоверен. Сначала пересчёт."
        )
    return (
        "ПРОГНОЗ НЕ ПОКАЗАН: данных недостаточно для прогноза, которому можно доверять. "
        "Количество ниже — заглушка."
    )


# ==========================================================================
# Инвариант 4: ограничение по сроку годности видно и объяснено
# ==========================================================================


def _cap(notice: CapNotice) -> str:
    if notice.kind is CapKind.SHELF_LIFE:
        subject = notice.subject or "этот товар"
        days = "" if notice.days is None else f" до {_days(notice.days)}"
        return (
            f"УРЕЗАНО НАМЕРЕННО{days} покрытия — срок годности «{subject}». "
            "Прогноз просил больше, но разница испортилась бы раньше, чем её успели бы "
            "использовать. Поднимать это количество нельзя: так возвращается ровно тот "
            "убыток, который ограничение и предотвращает. Правильный ответ — заказывать "
            "чаще, а не больше."
        )
    if notice.kind is CapKind.SEASON_END:
        subject = notice.subject or "сезон"
        days = "" if notice.days is None else f" до {_days(notice.days)}"
        return (
            f"УРЕЗАНО НАМЕРЕННО{days} покрытия — сезон «{subject}» заканчивается. "
            "Остаток пришёлся бы на дни, когда напитка уже нет в меню."
        )
    if notice.kind is CapKind.OUT_OF_SEASON:
        subject = notice.subject or "сезон"
        return (
            f"НЕ ЗАКАЗАНО: сезон «{subject}» не идёт. Это закупка под напиток, которого нет в меню."
        )
    if notice.kind is CapKind.TOP_UP_MINIMUM:
        return (
            "ДОБОР ДО МИНИМАЛЬНОЙ СУММЫ ЗАКАЗА. Прогноз этого не просил: строка "
            "добавлена только чтобы поставщик вообще отгрузил заказ."
        )
    if notice.kind is CapKind.TOP_UP_FREE_DELIVERY:
        return (
            "ДОБОР ДО БЕСПЛАТНОЙ ДОСТАВКИ. Прогноз этого не просил: строка добавлена "
            "только чтобы не платить за доставку."
        )
    days = "" if notice.days is None else f" до {_days(notice.days)}"
    return f"УРЕЗАНО НАМЕРЕННО{days} покрытия."


# ==========================================================================
# Пояснения к заказу: код -> фраза (spec 5.4)
# ==========================================================================
#
# `purchase_order.notes` -- английская проза для служебного чтения, и раньше бот её просто
# ВЫБРАСЫВАЛ: перевести прозу на ходу нельзя, значит владелица видела количество без
# объяснения, откуда оно. Spec 5.4 запрещает молча менять заказ, а фраза, которую нельзя
# прочитать, — это и есть молчание.
#
# Теперь каждое пояснение приходит кодом (`OrderNoteKind`), и русская фраза пишется здесь.
# Значение `None` — сознательное: это рабочая выкладка для служебного отчёта
# (`cafeops simulate`), а не решение, которое владелице нужно принять. Пропуск помечен
# явно, чтобы «нет фразы» нельзя было спутать с «забыли добавить».

_ORDER_NOTE: dict[OrderNoteKind, str | None] = {
    # Ограничения по сроку годности и сезону: у каждой строки уже есть своя причина выше.
    OrderNoteKind.CAP_LINE: None,
    OrderNoteKind.CAP_SUMMARY: None,
    OrderNoteKind.OUT_OF_SEASON_NOT_ORDERED: (
        "СЕЗОННАЯ ПОЗИЦИЯ НЕ ЗАКАЗАНА: сезон не идёт. Это была бы закупка под напиток, "
        "которого нет в меню."
    ),
    # Пар-уровни.
    OrderNoteKind.PAR_FLOOR_RAISED: (
        "КОЛИЧЕСТВО ПОДНЯТО ВЫШЕ ПРОГНОЗА, чтобы остаток дошёл до нижней границы "
        "пар-уровня. Прогноз просил меньше — разницу заказывает не он, а пар-уровень."
    ),
    OrderNoteKind.PAR_FLOOR_BELOW_CAP: (
        "Нижняя граница пар-уровня требует больше, чем влезает в срок годности. "
        "НЕ подняли: срок годности важнее пар-уровня, а границу, которая больше того, "
        "что успевает уйти, нужно править, а не закупать."
    ),
    OrderNoteKind.PAR_CEILING_CUT: (
        "КОЛИЧЕСТВО УРЕЗАНО верхней границей пар-уровня — заказ сузил не прогноз, а пар-уровень."
    ),
    OrderNoteKind.PAR_CEILING_BELOW_DEMAND: (
        "ВЕРХНЯЯ ГРАНИЦА ПАР-УРОВНЯ НИЖЕ СПРОСА за окно покрытия: при таком потолке "
        "дефицит будет независимо от прогноза. Это не «осторожный заказ», это "
        "запланированный дефицит — пар-уровень нужно поправить."
    ),
    OrderNoteKind.BELOW_PAR_FLOOR_NOT_ORDERED: None,
    OrderNoteKind.BELOW_PAR_FLOOR_SUMMARY: (
        "ЕСТЬ ПОЗИЦИИ НИЖЕ НИЖНЕЙ ГРАНИЦЫ ПАР-УРОВНЯ, по которым ничего не расходуется — "
        "они НЕ заказаны. Либо граница выставлена неверно, либо позиция не продаётся. "
        "Закупать под границу, под которой нет расхода, — это тратить деньги на ошибку "
        "в данных."
    ),
    OrderNoteKind.NOTHING_NEEDED: None,
    OrderNoteKind.PAR_DATA_ERROR: (
        "ОШИБКА В ПАР-УРОВНЕ: нижняя граница выше верхней. Заказ ограничен верхней, "
        "чтобы не разогнался, но сам пар-уровень нужно поправить."
    ),
    OrderNoteKind.PACK_DATA_ERROR: (
        "ОШИБКА В КАРТОЧКЕ ТОВАРА: размер упаковки не положительный, посчитать количество "
        "упаковок нельзя. Позиция не заказана — нужно поправить карточку у поставщика."
    ),
    OrderNoteKind.SKIPPED_NO_PAR: (
        "ЧАСТЬ ПОЗИЦИЙ НЕ РАССМАТРИВАЛАСЬ: у них нет пар-уровня или ещё не заработан "
        "автозаказ. Это не «ничего не нужно», это «не считалось»."
    ),
    # Два разных добора.
    OrderNoteKind.TOP_UP_APPLIED: None,
    OrderNoteKind.TOP_UP_IMPOSSIBLE: (
        "ДОБРАТЬ ДО ЦЕЛИ НЕ ВЫШЛО: ни одна позиция не подходит для добора. Решать вам — "
        "заказать как есть, отложить или договориться с поставщиком."
    ),
    OrderNoteKind.TOP_UP_SHORT_OF_TARGET: (
        "ПОСЛЕ ДОБОРА ДО ЦЕЛИ ВСЁ РАВНО НЕ ХВАТАЕТ: все подходящие позиции уже на своём "
        "максимуме. Дальше добивать нельзя — это уже закупка ради закупки."
    ),
    OrderNoteKind.TOP_UP_EXCLUDED_PERISHABLE: (
        "В ДОБОР НЕ ВЗЯТО НИЧЕГО ПОРТЯЩЕГОСЯ (инвариант 5). Купить то, что испортится, "
        "ради минимальной суммы или бесплатной доставки — это купить будущий убыток."
    ),
    OrderNoteKind.TOP_UP_EXCLUDED_SHELF_LIFE_UNKNOWN: (
        "Часть позиций не взята в добор, потому что срок годности у них не указан. "
        "«Неизвестно» — это не «не портится», и добор — не место это выяснять."
    ),
    OrderNoteKind.TOP_UP_EXCLUDED_NO_VELOCITY: (
        "Часть позиций не взята в добор, потому что по ним нет зафиксированного расхода. "
        "Закупать то, что не продаётся, ради минимума поставщика — не экономия."
    ),
    OrderNoteKind.TOP_UP_POOL_EMPTY: (
        "Добирать было нечем: не нашлось ни одной непортящейся позиции категории B "
        "с измеренным расходом."
    ),
    OrderNoteKind.MINIMUM_NOT_APPLICABLE: (
        "Ничего не нужно, поэтому минимум поставщика не применяется: минимум — это "
        "условие для заказа, а не повод его сделать."
    ),
    OrderNoteKind.FREE_DELIVERY_CLEARED: None,
    OrderNoteKind.FREE_DELIVERY_FEE_PAID: None,
    # Факты о самом прогоне.
    OrderNoteKind.PLACEHOLDER_TERMS: None,
    OrderNoteKind.SOURCING_LINE_MOVED: (
        "ЧАСТЬ СТРОК УШЛА К ДРУГОМУ ПОСТАВЩИКУ, потому что там дешевле за единицу. "
        "Заказ после этого пересчитан заново — вместе с минимумом и добором."
    ),
    OrderNoteKind.CUTOFF_MISSED: (
        "ВРЕМЯ ОТСЕЧЕНИЯ ПРОПУЩЕНО: заказ оформлен позже, чем поставщик принимает на "
        "ближайшую машину, поэтому поставка сдвинулась. Окно покрытия из-за этого длиннее, "
        "а значит и каждое количество в заказе больше, чем было бы нужно. Десять минут "
        "раньше — и заказ ушёл бы предыдущим рейсом."
    ),
    OrderNoteKind.WHAT_IF_MINIMUM: (
        "Заказ посчитан против минимума, заданного вручную, а не того, что записан "
        "у поставщика. Это прикидка «что если», а не обычный заказ."
    ),
    OrderNoteKind.NOT_COUNTED: (
        "НЕ ЗАКАЗАНО: часть позиций ни разу не пересчитывали, поэтому их остаток — "
        "сумма по журналу без реального пересчёта под ней. Один пересчёт — и позиция "
        "попадёт в следующий заказ."
    ),
    OrderNoteKind.CHECKLIST_REQUEST: None,
    OrderNoteKind.OTHER: None,
}


def _order_notes(view: OrderView) -> list[str]:
    """Пояснения к заказу, по кодам. Ни одной английской фразы наружу.

    Порядок — тот, в каком решения принимались (`purchase_order.note_codes`), а не
    алфавитный: сначала то, что изменило количества, потом то, что объясняет условия.
    """
    rows = [text for kind in view.notes if (text := _ORDER_NOTE.get(kind)) is not None]
    if not rows:
        return []
    return ["", "ПОЧЕМУ ЗАКАЗ ТАКОЙ:", *(f"  – {row}" for row in rows)]


# ==========================================================================
# Заказ поставщику
# ==========================================================================


_CHANNEL_NAME: dict[OrderChannel, str] = {
    OrderChannel.MANUAL: "самовывоз, список покупок",
    OrderChannel.BROWSER_AGENT: "сайт поставщика, корзина собирается автоматически",
    OrderChannel.EMAIL: "заказ письмом",
    OrderChannel.PORTAL: "портал поставщика",
    OrderChannel.EDI: "EDI",
}

_STATUS: dict[POStatus, str] = {
    POStatus.DRAFT: "черновик, ждёт вас",
    POStatus.PENDING_CONFIRM: "ждёт подтверждения",
    POStatus.CONFIRMED: "подтверждён",
    POStatus.SENT: "передан в канал",
    POStatus.RECEIVED: "принят",
    POStatus.CANCELLED: "отменён",
}


def _order_line(line: OrderLineView, index: int) -> list[str]:
    rows = [
        f"{index}. {line.ingredient_name} — {_packs(line.packs)} "
        f"x {_qty(line.pack_size, line.pack_unit)} = {money(line.line_total_pence)}"
    ]
    if line.adjusted:
        rows.append(
            f"    вы изменили: было предложено {_packs(line.suggested_packs)}, "
            f"сейчас {_packs(line.packs)}"
        )
    if line.low_confidence is not None:
        # Инвариант 9. Потребность тоже не показываем: need = прогноз минус остаток,
        # и назвать её значит вернуть на экран скрытое число.
        rows.append(f"    {_low_confidence(line.low_confidence)}")
    elif line.need_qty is not None and not line.is_top_up:
        # У строки добора потребность отрицательная -- запас уже покрыт, её и добавили
        # не по прогнозу. Показать «-368 шт» значило бы предложить человеку считать
        # смысл там, где его нет; причину добора он читает строкой ниже.
        rows.append(f"    расчётная потребность {_qty(line.need_qty, line.unit)}")
    if line.cap is not None:
        rows.append(f"    {_cap(line.cap)}")
    if line.checklist_requested_by is not None:
        # Категория C не считается (spec 4.7): это количество назвал человек. Показать
        # его так же, как посчитанное, значило бы выдать догадку за расчёт.
        rows.append(
            f"    ИЗ ЧЕК-ЛИСТА: количество назвал(а) «{line.checklist_requested_by}». "
            "Прогноза по этой позиции нет и быть не может — категория C не считается, "
            "поэтому цифру выбрал человек, а не система."
        )
    return rows


def _order_top_ups(view: OrderView) -> list[str]:
    """Добор назван по именам. «Она не должна узнать про сироп из счёта»."""
    top_ups = view.top_up_lines
    if not top_ups and not view.min_order_topped_up:
        return []
    if not top_ups:
        return [
            "",
            "ДОБОР ДО МИНИМУМА: заказ был добран, но строки добора в нём не отмечены. "
            "Проверьте состав вручную, прежде чем подтверждать.",
        ]
    total = sum(line.line_total_pence for line in top_ups)
    rows = [
        "",
        f"ДОБОР — {_lines_word(len(top_ups))} на {money(total)}, которые прогноз НЕ просил:",
    ]
    rows += [
        f"  – {line.ingredient_name}: {_packs(line.packs)}, {money(line.line_total_pence)}"
        for line in top_ups
    ]
    rows.append(
        "Это единственная часть заказа, которая нужна поставщику, а не кофейне. "
        "Если минимум того не стоит — уберите эти строки кнопкой «−»."
    )
    return rows


def _order_channel_note(view: OrderView) -> list[str]:
    if view.channel is OrderChannel.BROWSER_AGENT or view.stages_basket:
        return [
            "",
            "КАНАЛ: корзина на сайте поставщика собирается автоматически и "
            "останавливается ДО оплаты. После подтверждения вы получите сообщение "
            "«корзина готова» — последнюю кнопку нажимаете вы. Заказ этим ещё не "
            "отправлен.",
        ]
    if view.channel is OrderChannel.MANUAL:
        return [
            "",
            "КАНАЛ: это список покупок. Никуда ничего не отправляется — нужно прийти и купить.",
        ]
    return [
        "",
        f"КАНАЛ: {_CHANNEL_NAME.get(view.channel, view.channel.value)} — пока не "
        "настроен. После подтверждения заказ останется подтверждённым, а отправить его "
        "нужно вручную.",
    ]


def order_card(view: OrderView) -> str:
    """Карточка одного заказа: то, что человек читает перед нажатием «Подтвердить»."""
    rows = [
        f"ЗАКАЗ №{view.po_id} — {view.supplier_name}",
        f"поставка {_d(view.target_delivery_date)} ({_weekdays(view.delivery_weekdays)}), "
        f"срок поставки {_days(view.lead_time_days)}",
        f"состояние: {_STATUS.get(view.status, view.status.value)}",
        "",
    ]

    if not view.lines:
        rows.append("В заказе нет строк — подтверждать нечего.")
        return "\n".join(rows)

    for index, line in enumerate(view.lines, 1):
        rows += _order_line(line, index)

    rows.append("")
    rows.append(f"товары: {money(view.goods_pence)}")
    if view.delivery_fee_pence > 0:
        threshold = view.free_delivery_threshold_pence
        if threshold is not None and view.goods_pence >= threshold:
            rows.append(
                f"доставка бесплатно: {money(view.goods_pence)} перекрывает порог "
                f"{money(threshold)}"
            )
        else:
            rows.append(f"доставка: {money(view.delivery_fee_pence)}")
    rows.append(f"ИТОГО: {money(view.total_pence)}")

    if view.min_order_pence > 0:
        if view.meets_minimum:
            rows.append(f"минимум поставщика {money(view.min_order_pence)} — выполнен")
        else:
            rows.append(
                f"МИНИМУМ {money(view.min_order_pence)} НЕ ВЫПОЛНЕН. Ниже минимума "
                "поставщик не отгружает ничего: выбор не «заказать меньше», а "
                "«не заказать вовсе». Решать вам."
            )

    capped = view.capped_lines
    if capped:
        rows.append("")
        rows.append(
            f"НАМЕРЕННО МЕНЬШЕ, ЧЕМ ПРОСИЛ ПРОГНОЗ — {_lines_word(len(capped))}: "
            + ", ".join(line.ingredient_name for line in capped)
            + ". Причина у каждой строки выше. Поднимать эти количества — значит "
            "покупать то, что испортится."
        )

    rows += _order_top_ups(view)

    low = view.low_confidence_lines
    if low:
        rows.append("")
        rows.append(
            f"БЕЗ ПРОГНОЗА — {_lines_word(len(low))}: "
            + ", ".join(line.ingredient_name for line in low)
            + ". У этих строк количество не опирается на прогноз, которому можно "
            "доверять; причина указана вместо цифры прогноза."
        )

    checklist = view.checklist_lines
    if checklist:
        rows.append("")
        rows.append(
            f"ИЗ ЧЕК-ЛИСТА КАТЕГОРИИ C — {_lines_word(len(checklist))}: "
            + ", ".join(line.ingredient_name for line in checklist)
            + ". Эти строки попали в заказ потому, что кто-то отметил «заканчивается» "
            "и сам назвал количество. Система их не считала и посчитать не может."
        )

    rows += _order_notes(view)
    rows += _order_channel_note(view)

    if view.terms_are_placeholders:
        rows.append("")
        rows.append(
            f"УСЛОВИЯ «{view.supplier_name}» ВЫДУМАНЫ. Срок поставки, дни доставки, "
            "время отсечения, минимум и порог бесплатной доставки никто с поставщиком "
            "не подтверждал. Окно покрытия — а значит и каждое количество в этом "
            "заказе — держится на этих догадках. Подтвердите условия у поставщика, "
            "прежде чем доверять числам."
        )

    rows.append("")
    rows.append(
        "Ничего не заказано, пока вы не нажали «Подтвердить». Кнопками «+» и «−» "
        "меняйте упаковки; подтверждение записывается на ваше имя."
    )
    return "\n".join(rows)


def order_nothing_to_confirm() -> str:
    return (
        "Черновиков заказов нет — подтверждать нечего.\n"
        "Черновики появляются сами, до времени отсечения каждого поставщика."
    )


def order_confirmed(view: OrderView) -> str:
    who = view.confirmed_by or "—"
    when = "" if view.confirmed_at is None else f" в {_dt(view.confirmed_at)}"
    rows = [
        f"Заказ №{view.po_id} «{view.supplier_name}» ПОДТВЕРЖДЁН — записано на «{who}»{when}.",
        f"{_lines_word(len(view.lines))}, итого {money(view.total_pence)}.",
    ]
    if view.channel is OrderChannel.BROWSER_AGENT or view.stages_basket:
        rows.append(
            "Дальше собирается корзина на сайте поставщика. Заказ ещё НЕ отправлен: "
            "последнюю кнопку нажимаете вы."
        )
    elif view.channel is OrderChannel.MANUAL:
        rows.append("Список покупок готов. Покупка происходит лично.")
    return "\n".join(rows)


def basket_job_finished(notice: BasketJobNotice) -> str:
    """Сообщение, когда сборка корзины закончилась. Никогда не «заказано»."""
    order = f" (заказ №{notice.po_id})" if notice.po_id is not None else ""
    if notice.status is BrowserJobStatus.SUCCEEDED:
        rows = [f"КОРЗИНА ГОТОВА — {notice.supplier_name}{order}."]
        if notice.lines_short:
            rows.append(
                f"Внимание: в корзине не хватает позиций — {notice.lines_short} из "
                f"{notice.lines_wanted}. Проверьте перед оплатой."
            )
        if notice.subtotal_seen_pence is not None:
            rows.append(f"Сумма в корзине: {money(notice.subtotal_seen_pence)}.")
        elif notice.total_expected_pence is not None:
            rows.append(f"Ожидаемая сумма: {money(notice.total_expected_pence)}.")
        rows.append(
            "Проверьте корзину и оплатите сами. ЗАКАЗ НЕ ОТПРАВЛЕН — после оплаты "
            "отметьте его отправленным в бэк-офисе."
        )
        if notice.basket_url:
            rows.append(notice.basket_url)
        return "\n".join(rows)
    if notice.status is BrowserJobStatus.NEEDS_HUMAN:
        rows = [
            f"Сборка корзины «{notice.supplier_name}»{order} остановлена: нужен человек "
            "(вход, код подтверждения или проверка «я не робот»).",
        ]
    else:
        rows = [f"Сборка корзины «{notice.supplier_name}»{order} не удалась."]
    if notice.reason:
        rows.append(f"Причина: {notice.reason}")
    rows.append("Заказ НЕ отправлен — оформите его вручную или запустите сборку ещё раз.")
    return "\n".join(rows)


def order_dispatched(view: DispatchView) -> str:
    """Никогда не «заказано», если канал требует человека."""
    if view.staged_job_id is not None:
        if view.staged_basket_url:
            rows = [
                f"КОРЗИНА ГОТОВА — {view.supplier_name}, {_items(view.items)} на "
                f"{money(view.total_pence)}. Откройте ссылку, проверьте корзину и "
                "оплатите сами. ЗАКАЗ НЕ ОТПРАВЛЕН.",
                view.staged_basket_url,
            ]
        else:
            rows = [
                f"Корзина «{view.supplier_name}» собирается на сайте поставщика "
                f"(задание №{view.staged_job_id}), {_items(view.items)} на "
                f"{money(view.total_pence)}.",
                "Сборка остановится ДО оплаты. Когда корзина будет готова, придёт "
                "сообщение. ЗАКАЗ НЕ ОТПРАВЛЕН.",
            ]
        rows.append(
            f"Когда оплатите на сайте — отметьте заказ №{view.po_id} отправленным в бэк-офисе."
        )
        return "\n".join(rows)
    if view.staging_refused is not None:
        rows = [
            f"Корзина «{view.supplier_name}» НЕ собрана: {view.staging_refused}",
            f"Заказ №{view.po_id} подтверждён, но НЕ отправлен — оформите его вручную.",
        ]
        if view.target_url:
            rows.append(view.target_url)
        return "\n".join(rows)
    if view.channel is OrderChannel.BROWSER_AGENT:
        if view.succeeded:
            rows = [
                f"КОРЗИНА ГОТОВА — {view.supplier_name}, {_items(view.items)} на "
                f"{money(view.total_pence)}.",
                "Проверьте корзину и нажмите кнопку оплаты сами. ЗАКАЗ НЕ ОТПРАВЛЕН.",
            ]
        else:
            rows = [
                f"Корзина ещё НЕ собрана: автоматический сбор для «{view.supplier_name}» "
                "пока не подключён.",
                f"Инструкция из {view.instruction_steps} шагов подготовлена и сохранена "
                f"к заказу №{view.po_id}.",
                "Заказ подтверждён, но НЕ отправлен — оформить нужно вручную.",
            ]
        if view.target_url:
            rows.append(view.target_url)
        return "\n".join(rows)

    if view.channel is OrderChannel.MANUAL:
        return (
            f"Список покупок готов — {view.supplier_name}, {_items(view.items)} на "
            f"{money(view.total_pence)}.\n"
            "Отправлять некуда: это поход в магазин. Когда принесёте — оформите "
            "приёмку, /delivery."
        )

    return (
        f"Канал «{_CHANNEL_NAME.get(view.channel, view.channel.value)}» для "
        f"«{view.supplier_name}» не настроен, поэтому заказ НЕ отправлен.\n"
        f"Заказ №{view.po_id} остаётся подтверждённым — отправьте его вручную."
    )


# ==========================================================================
# Пересчёт
# ==========================================================================

BTN_COUNT_SKIP = "Пропустить"
BTN_COUNT_STOP = "Закончить пересчёт"


def count_intro(kind: CountSessionKind, items: Sequence[CountItemView]) -> str:
    if kind is CountSessionKind.EXPRESS_A:
        head = (
            f"БЫСТРЫЙ ПЕРЕСЧЁТ, категория A — {_items(len(items))}. "
            "Это то, на что опирается автозаказ."
        )
    else:
        head = f"ПОЛНЫЙ ПЕРЕСЧЁТ — {_items(len(items))}."
    return "\n".join(
        [
            head,
            "",
            "По каждой позиции пришлите число — сколько фактически стоит на полке, "
            "в указанной единице. Не нужно ничего вычитать и прибавлять: "
            "фактический пересчёт и есть источник истины, а расчётный остаток рядом "
            "показан только чтобы вы увидели расхождение.",
            f"«{BTN_COUNT_SKIP}» — если сейчас не добрались; «{BTN_COUNT_STOP}» — прервать.",
        ]
    )


def count_nothing_due(kind: CountSessionKind) -> str:
    what = "категории A" if kind is CountSessionKind.EXPRESS_A else "отслеживаемых позиций"
    return f"Пересчитывать нечего: среди {what} нет ни одной просроченной по срокам пересчёта."


def count_prompt(item: CountItemView, *, index: int, total: int) -> str:
    line = item.stock
    rows = [
        f"[{index}/{total}] {item.name} [{_TIERS[item.tier]}]",
        f"{_stock_figure(line)}",
        _basis(line),
    ]
    if line.batch_qty > 0:
        rows.append(f"по партиям: {_qty(line.batch_qty, line.unit)}")
    if abs(line.unbatched_qty) > Decimal("0.001") and line.batch_qty > 0:
        rows.append(
            f"не привязано ни к одной партии: {_qty(line.unbatched_qty, line.unit)} — "
            "эта часть не может ни просрочиться, ни попасть в списание"
        )
    if line.soonest_expiry_days is not None:
        if line.soonest_expiry_days < 0:
            rows.append(f"есть партия, просроченная на {_days(-line.soonest_expiry_days)}")
        else:
            rows.append(f"ближайший срок годности: через {_days(line.soonest_expiry_days)}")
    rows.append("")
    rows.append(f"Сколько фактически ({_UNITS[item.unit]})?")
    return "\n".join(rows)


_VERDICT: dict[DriftVerdict, str] = {
    DriftVerdict.ELIGIBLE: "расхождение в норме (меньше 10%)",
    DriftVerdict.TUNE_WASTE_FACTOR: "расхождение 10–15%: пора править коэффициент потерь",
    DriftVerdict.FORCE_MANUAL: "расхождение больше 15%: числам доверять нельзя",
}

_CAUSE: dict[DriftCause, str] = {
    DriftCause.NEGLIGIBLE: (
        "разница мала на фоне расхода за период: ни рецепт, ни размер заказа править не "
        "нужно. Большой процент при маленьком остатке — это про маленькую базу, а не "
        "про большую потерю"
    ),
    DriftCause.EXPIRY: (
        "разницу объясняют списания по сроку годности — значит закупается ЛИШНЕЕ, "
        "а не рецепт неверен. Лечится меньшим заказом, но чаще"
    ),
    DriftCause.MEASUREMENT: (
        "списаний по сроку не хватает, чтобы объяснить разницу — значит дело в рецепте "
        "или в коэффициенте потерь. Урезать заказ здесь НЕЛЬЗЯ: это даст дефицит, "
        "а разница останется"
    ),
    DriftCause.MIXED: "причина смешанная: часть — списания по сроку, часть — рецепт",
}

_GATE: dict[GateAction, str] = {
    GateAction.GRANT: "автозаказ ВКЛЮЧЁН — право заработано двумя чистыми пересчётами подряд",
    GateAction.REVOKE: (
        "автозаказ ВЫКЛЮЧЕН: основание, на котором он был включён, больше не действует"
    ),
    GateAction.HOLD: "автозаказ без изменений",
}

#: ПОЧЕМУ автозаказ отобрали. Раньше `reason` была английской прозой, и бот мог сказать
#: только «выключен» — а у двух причин разные ответы: по одной надо пересчитать, по другой
#: позиция вообще не должна была быть в автозаказе (`ARCHITECTURE.md` 8A.3).
_REVOKE_CAUSE: dict[RevokeCause, str] = {
    RevokeCause.DRIFT_ABOVE_TOLERANCE: (
        "расхождение выше 15%: расчётным остаткам по этой позиции доверять нельзя, "
        "а заказ по недостоверному остатку — это и есть те самые £200 молока, "
        "которое никому не нужно"
    ),
    RevokeCause.DRIFT_IN_TUNING_BAND: (
        "расхождение попало в полосу 10–15%. Автозаказ держался на двух чистых "
        "пересчётах подряд, и один из них больше не чистый — значит основание истекло. "
        "Поправьте коэффициент потерь и пересчитайте ещё раз"
    ),
    RevokeCause.STREAK_BROKEN: ("чистых пересчётов подряд стало меньше, чем нужно для автозаказа"),
    RevokeCause.TIER_NOT_A: (
        "позиция больше не в категории A, а вне категории A автозаказа не бывает"
    ),
    RevokeCause.PAR_LEVEL_REMOVED: (
        "у позиции больше нет пар-уровня: нечем задать минимум и максимум, значит нечего и считать"
    ),
    RevokeCause.NO_OBSERVATION: (
        "истории расхождений по этой позиции больше нет, а автозаказ без истории не существует"
    ),
}


def _revoke(cause: RevokeCause | None) -> list[str]:
    """Что именно отобрало автозаказ. Материальное изменение, а не примечание.

    Автозаказ выключился — значит заказы, которые собирались сами, собираться перестали.
    Узнать об этом по тому, что ничего не приехало, — худший способ.
    """
    if cause is None:
        return []
    return [
        "АВТОЗАКАЗ ПО ЭТОЙ ПОЗИЦИИ ВЫКЛЮЧЕН — это меняет поведение системы: черновики "
        "по ней больше не собираются сами, пока право не заработано заново.",
        f"причина: {_REVOKE_CAUSE[cause]}",
    ]


def count_result(result: CountResultView) -> str:
    rows = [
        f"{result.name}: записан фактический пересчёт {_qty(result.counted_qty, result.unit)}.",
        f"расчётный остаток был {_qty(result.theoretical_qty, result.unit)} — "
        "теперь пересчёт заменил его как точку отсчёта.",
    ]
    if result.drift_pct is None:
        rows.append(
            "Это первый пересчёт этой позиции: сравнивать не с чем, поэтому "
            "расхождение не считается. Оно появится со второго пересчёта."
        )
    else:
        rows.append(f"расхождение {_pct(result.drift_pct)}")
        if result.verdict is not None:
            rows.append(_VERDICT[result.verdict])
        if result.cause is not None:
            rows.append(_CAUSE[result.cause])
    if result.back_dated:
        rows.append(
            "Пересчёт задним числом: более поздний пересчёт уже есть, поэтому "
            "сегодняшний остаток он не переопределяет."
        )
    rows.append(_GATE[result.gate_action])
    rows += _revoke(result.revoke_cause)
    if result.gate_action is not GateAction.GRANT and result.required_streak > 0:
        rows.append(
            f"чистых пересчётов подряд: {result.clean_streak} из {result.required_streak} "
            "нужных для автозаказа"
        )
    if result.suggested_waste_factor is not None:
        rows.append(
            f"предложение: коэффициент потерь {result.current_waste_factor} → "
            f"{result.suggested_waste_factor}. Это предложение, а не изменение — "
            "применять решаете вы."
        )
    if result.alert_level is GateAlertLevel.ALARM:
        rows.append(
            "ТРЕВОГА: пока расхождение такое, любые расчётные остатки по этой позиции "
            "недостоверны, и автозаказ по ней запрещён."
        )
    elif result.alert_level is GateAlertLevel.NOTICE:
        # Отдельный уровень, а не та же тревога: «вы потеряли автозаказ» и «вашим числам
        # нельзя верить» — разные утверждения, и склеивать их в один флаг значит потерять
        # оба. Spec 5.2 требует тревогу только выше 15%; молчать про отзыв она не требует.
        rows.append(
            "ВАЖНО: это не «числам нельзя верить» — расхождение ещё в рабочей полосе. "
            "Но право на автозаказ отозвано, и вернуть его можно только двумя чистыми "
            "пересчётами подряд."
        )
    return "\n".join(rows)


def count_done(*, counted: int, skipped: int, total: int) -> str:
    rows = [f"Пересчёт закончен: {counted} из {total} записано."]
    if skipped:
        rows.append(
            f"{skipped} пропущено — по ним остаток остаётся расчётным, без физического основания."
        )
    return "\n".join(rows)


# ==========================================================================
# Чек-лист категории C
# ==========================================================================

BTN_CHECKLIST_OK = "Хватает"
BTN_CHECKLIST_LOW = "Заканчивается"
BTN_CHECKLIST_SKIP = "Пропустить"
BTN_CHECKLIST_NO_ORDER = "Не заказывать"


def checklist_intro(items: Sequence[ChecklistItemView]) -> str:
    if not items:
        return "Чек-лист категории C пройден: всё отмечено за последнюю неделю, спрашивать нечего."
    return "\n".join(
        [
            f"ЧЕК-ЛИСТ, категория C — {_items(len(items))}.",
            "",
            "По каждой позиции сначала только «хватает» или «заканчивается»: категория C "
            "не считается и не прогнозируется, поэтому считать тут нечего.",
            "",
            "Если «заканчивается» — спрошу, сколько упаковок заказать. Число называете вы: "
            "прогноза по категории C нет, и придумывать его система не будет. "
            f"«{BTN_CHECKLIST_NO_ORDER}» — если отметить, но пока не заказывать.",
        ]
    )


def checklist_prompt(item: ChecklistItemView, *, index: int, total: int) -> str:
    rows = [f"[{index}/{total}] {item.name}"]
    if item.last_answered_at is None:
        rows.append("раньше не отмечалось ни разу")
    else:
        was = "«заканчивается»" if item.last_was_low else "«хватает»"
        rows.append(f"прошлый ответ: {was}, {_d(item.last_answered_at)}")
    rows.append("")
    rows.append("Хватает?")
    return "\n".join(rows)


def checklist_result(item: ChecklistItemView) -> str:
    if item.last_was_low:
        return f"{item.name}: отмечено «заканчивается»."
    return f"{item.name}: отмечено «хватает»."


def checklist_order_prompt(item: ChecklistItemView) -> str:
    """Единственный вопрос, который категория C вправе задать про количество.

    Спрашиваем без подсказки: ни «в прошлый раз брали N», ни нижней границы пар-уровня —
    их у категории C нет, и любая такая цифра была бы догадкой в костюме расчёта
    (spec 4.7). Число целиком на человеке, и строка потом это скажет прямо.
    """
    return "\n".join(
        [
            f"{item.name}: сколько упаковок заказать?",
            "",
            "Цифру подсказать не могу и не буду: категория C не считается и не "
            "прогнозируется, так что число здесь — ваше решение, и в заказе будет "
            "написано, что его назвал человек.",
            f"Пришлите целое число упаковок или нажмите «{BTN_CHECKLIST_NO_ORDER}».",
        ]
    )


def checklist_ordered(request: ChecklistOrderRequest) -> str:
    """Что произошло после названного количества. Черновик, не заказ (инвариант 1)."""
    rows = [
        f"{request.ingredient_name}: {_packs(request.packs)} "
        f"x {_qty(request.pack_size, request.pack_unit)} = "
        f"{money(request.line_total_pence)} — добавлено в черновик заказа "
        f"№{request.po_id} «{request.supplier_name}».",
    ]
    rows.append(
        "новый черновик создан"
        if request.order_created
        else "строка встала в уже открытый черновик этого поставщика"
    )
    rows.append(f"ожидаемая поставка: {_d(request.target_delivery_date)}")
    rows.append(
        "В строке записано, что количество назвали вы: прогноза по этой позиции нет, "
        "и система не будет делать вид, что он есть."
    )
    if request.terms_are_placeholders:
        rows.append(
            f"Условия «{request.supplier_name}» никто с поставщиком не подтверждал, "
            "поэтому дата поставки выше — догадка."
        )
    rows.append("НИЧЕГО НЕ ЗАКАЗАНО, пока вы не нажали «Подтвердить»: /orders")
    return "\n".join(rows)


def checklist_order_refused(name: str) -> str:
    """Отказ, а не строка с придуманными условиями.

    Без карточки товара нет ни размера упаковки, ни цены. У категории C нет прогноза,
    которым потом можно было бы поправить выдумку, поэтому честнее отказаться.
    """
    return (
        f"«{name}» в заказ поставить не могу: у этой позиции нет ни одной карточки "
        "товара у поставщика, значит нет ни размера упаковки, ни цены.\n"
        "Выдумывать упаковку я не буду — отметку «заканчивается» записал, "
        "а карточку товара нужно заполнить."
    )


def checklist_done(*, answered: int, low: int, total: int, ordered: int = 0) -> str:
    rows = [f"Чек-лист закончен: {answered} из {total} отмечено."]
    if low:
        rows.append(f"«Заканчивается»: {low}.")
    if ordered:
        rows.append(
            f"В черновики заказов добавлено: {_lines_word(ordered)} — с пометкой, что "
            "количество назвал человек. Подтверждать вам: /orders"
        )
    elif low:
        rows.append(
            "Количество ни по одной не названо, поэтому в заказ ничего не встало. "
            "Отметка сохранена — она будет в утренней сводке."
        )
    return "\n".join(rows)


# ==========================================================================
# Приёмка поставки: срок годности вводится здесь, и только здесь
# ==========================================================================

BTN_DELIVERY_NO_DATE = "Даты на упаковке нет"
BTN_DELIVERY_SKIP = "Пропустить позицию"


def delivery_nothing_expected() -> str:
    return (
        "Поставок не ждём: нет ни одного подтверждённого заказа.\n"
        "Если товар принесли без заказа (поход в магазин) — это отдельная приёмка, "
        "/adhoc."
    )


def delivery_intro(orders: Sequence[DeliveryOrderView]) -> str:
    rows = ["ПРИЁМКА ПОСТАВКИ", ""]
    for order in orders:
        outstanding = order.outstanding_lines
        rows.append(
            f"Заказ №{order.po_id} «{order.supplier_name}», поставка "
            f"{_d(order.target_delivery_date)} — ждём {_lines_word(len(outstanding))}"
        )
    rows += [
        "",
        "По каждой позиции спрошу две вещи: сколько упаковок пришло и какой срок "
        "годности стоит на упаковке.",
        "Дата — не формальность: именно она определяет, что списывать первым и что "
        "уйдёт в убыток. Без неё система подставит свою оценку, и это будет догадка.",
    ]
    return "\n".join(rows)


def delivery_qty_prompt(line: DeliveryLineView, *, index: int, total: int) -> str:
    rows = [
        f"[{index}/{total}] {line.ingredient_name}",
        f"заказано {_packs(line.ordered_packs)} x {_qty(line.pack_size, line.pack_unit)}",
    ]
    if line.expected_qty is not None:
        rows.append(f"ожидаем {_qty(line.expected_qty, line.unit)}")
    if line.already_received_qty > 0:
        rows.append(f"уже принято по этой строке: {_qty(line.already_received_qty, line.unit)}")
    rows.append(f"хранение: {_STORAGE[line.storage]}")
    rows.append("")
    rows.append("Сколько упаковок пришло?")
    return "\n".join(rows)


def delivery_expiry_prompt(line: DeliveryLineView, *, packs: int) -> str:
    """Главный вопрос всей приёмки."""
    rows = [
        f"{line.ingredient_name}: принимаю {_packs(packs)}.",
        "",
        "КАКОЙ СРОК ГОДНОСТИ НА УПАКОВКЕ?",
        "Формат: ДД.ММ или ДД.ММ.ГГГГ.",
    ]
    if line.default_shelf_life_days is None:
        rows.append(
            "У этой позиции срок годности не задан — она числится непортящейся. "
            f"Если дата на упаковке всё же есть, введите её; «{BTN_DELIVERY_NO_DATE}» — "
            "если действительно нет."
        )
    else:
        source = "и это ОЦЕНКА, а не измерение" if line.shelf_life_is_estimate else "по справочнику"
        rows.append(
            f"Если даты нет, подставлю {_days(line.default_shelf_life_days)} от сегодня "
            f"({source}). Именно это число решает, что уйдёт в списание, поэтому "
            "дата с упаковки всегда лучше."
        )
    if line.open_life_days is not None:
        rows.append(
            f"После вскрытия эта позиция живёт {_days(line.open_life_days)} — "
            "срок может наступить раньше даты на упаковке."
        )
    return "\n".join(rows)


_RECEIPT_ISSUE: dict[ReceiptIssue, str] = {
    ReceiptIssue.EXPIRY_ASSUMED: (
        "СРОК ГОДНОСТИ ПОДСТАВЛЕН, а не прочитан с упаковки. Партия помечена. "
        "Это та самая дата, по которой товар уйдёт в убыток, — её стоит заменить на "
        "настоящую"
    ),
    ReceiptIssue.EXPIRY_NOT_AFTER_RECEIPT: (
        "введённая дата не позже даты поставки: товар пришёл уже просроченным и "
        "ближайшая проверка его спишет. Записано как введено — проверьте упаковку"
    ),
    ReceiptIssue.OVER_DELIVERY: (
        "пришло больше, чем заказано. Записано полностью: товар всё равно на полке"
    ),
    ReceiptIssue.PRICE_FROM_CACHE: (
        "цена не указана, взята из справочника. Розничная закупка обычно дороже, "
        "поэтому будущее списание по этой партии занизит убыток"
    ),
    ReceiptIssue.NON_PERISHABLE_WITH_DATE: (
        "позиция числится непортящейся, но дата введена. Дату оставляю — человек с "
        "упаковкой в руках важнее справочника, а справочник стоит поправить"
    ),
    # Never rendered: `views.py` drops OTHER from `issues` and counts it instead, so the
    # message says «ещё N замечаний» rather than an empty bullet.
    ReceiptIssue.OTHER: "",
}


def delivery_receipt(view: ReceiptView) -> str:
    rows = [
        f"Принято: {view.ingredient_name}, {_qty(view.qty, view.unit)} "
        f"на {money(view.value_pence)}.",
        f"партия №{view.batch_id}",
    ]
    if view.expires_at is None:
        rows.append("срок годности не задан: позиция не портится")
    else:
        left = "" if view.days_left is None else f", осталось {_days(view.days_left)}"
        assumed = " (ПОДСТАВЛЕН)" if view.expiry_was_assumed else ""
        rows.append(f"срок годности: {_d(view.expires_at)}{left}{assumed}")
    if view.open_life_days is not None:
        rows.append(f"после вскрытия: {_days(view.open_life_days)}")
    for issue in view.issues:
        text = _RECEIPT_ISSUE.get(issue, "")
        if text:
            rows.append(f"— {text}")
    if view.unclassified_issues:
        rows.append(
            f"— ещё {view.unclassified_issues} "
            f"{_plural(view.unclassified_issues, 'замечание', 'замечания', 'замечаний')} "
            "записано в журнал приёмки"
        )
    if view.order_completed:
        rows.append("Заказ принят полностью и закрыт.")
    return "\n".join(rows)


# ==========================================================================
# Утренняя сводка
# ==========================================================================


def _digest_write_offs(rows: Sequence[WriteOffView]) -> list[str]:
    if not rows:
        return []
    priced = [row for row in rows if row.loss_pence is not None]
    total = sum((row.loss_pence or Decimal("0")) for row in priced)
    out = [
        "",
        f"СПИСАНИЕ ПО СРОКУ ГОДНОСТИ — {_items(len(rows))} на {money(total)}:",
    ]
    for row in rows[:8]:
        extra = " (истёк срок после вскрытия)" if row.expired_after_opening else ""
        assumed = " [дата была подставлена]" if row.expiry_was_assumed else ""
        out.append(
            f"  – {row.ingredient_name}: {_qty(row.qty, row.unit)}, "
            f"{money(row.loss_pence)}, просрочено {_days(row.days_overdue)}"
            f"{extra}{assumed}"
        )
    if len(rows) > 8:
        out.append(f"  … ещё {len(rows) - 8}")
    if len(priced) != len(rows):
        out.append(
            f"  {len(rows) - len(priced)} из них без цены — в сумму не включены "
            "(неизвестная цена не равна нулю)"
        )
    out.append("Эти деньги уже потеряны. Единственный вывод из них — частота и размер заказов.")
    return out


def _digest_short_dated(rows: Sequence[ExpiryLineView]) -> list[str]:
    if not rows:
        return []
    out = ["", f"СРОК ПОДХОДИТ — {_items(len(rows))}, это ещё можно продать:"]
    for row in sorted(rows, key=lambda r: r.days_left)[:8]:
        out.append(
            f"  – {row.ingredient_name}: {_qty(row.qty, row.unit)}, "
            f"{_days(row.days_left)} до {_d(row.expires_at)} ({money(row.value_pence)})"
        )
    if len(rows) > 8:
        out.append(f"  … ещё {len(rows) - 8}")
    return out


def _digest_orders(rows: Sequence[OrderView]) -> list[str]:
    if not rows:
        return ["", "Черновиков заказов нет."]
    out = ["", f"ЖДУТ ПОДТВЕРЖДЕНИЯ — {_items(len(rows))}:"]
    for view in rows:
        flags = []
        if view.capped_lines:
            flags.append(f"урезано по сроку годности: {len(view.capped_lines)}")
        if view.top_up_lines:
            flags.append(f"добор до минимума: {len(view.top_up_lines)}")
        if view.low_confidence_lines:
            flags.append(f"без прогноза: {len(view.low_confidence_lines)}")
        if not view.meets_minimum and view.min_order_pence > 0:
            flags.append("минимум не выполнен")
        if view.terms_are_placeholders:
            flags.append("условия поставщика выдуманы")
        tail = f" — {'; '.join(flags)}" if flags else ""
        out.append(
            f"  – №{view.po_id} {view.supplier_name}: {_lines_word(len(view.lines))}, "
            f"{money(view.total_pence)}, поставка {_d(view.target_delivery_date)}{tail}"
        )
    out.append("Ни одна строка не заказана, пока вы не подтвердили. /orders")
    return out


def _digest_deliveries(rows: Sequence[DeliveryOrderView]) -> list[str]:
    if not rows:
        return []
    out = ["", f"ЖДЁМ ПОСТАВКУ — {_items(len(rows))}:"]
    for order in rows:
        out.append(
            f"  – №{order.po_id} {order.supplier_name}, {_d(order.target_delivery_date)}: "
            f"{_lines_word(len(order.outstanding_lines))}"
        )
    out.append("Когда привезут — /delivery, и обязательно со сроком годности с упаковки.")
    return out


def _digest_drift(rows: Sequence[DriftAlertView]) -> list[str]:
    if not rows:
        return []
    out = ["", f"РАСХОЖДЕНИЯ — {_items(len(rows))}:"]
    for row in rows:
        parts = [f"  – {row.name}: расхождение {_pct(row.drift_pct)}"]
        if row.verdict is not None:
            parts.append(_VERDICT[row.verdict])
        parts.append(_GATE[row.gate_action])
        out.append(", ".join(parts))
        if row.cause is not None:
            out.append(f"      {_CAUSE[row.cause]}")
        for line in _revoke(row.revoke_cause):
            out.append(f"      {line}")
    return out


def _digest_revocations(rows: Sequence[RevocationView]) -> list[str]:
    """Позиции, У КОТОРЫХ ОТОБРАЛИ АВТОЗАКАЗ. Событие, а не состояние.

    Это единственный канал, который дойдёт до владелицы независимо от того, кто держал
    планшет при пересчёте. Автозаказ выключился — значит черновики по позиции больше не
    собираются сами, и узнать об этом по тому, что ничего не приехало, — худший способ
    (`ARCHITECTURE.md` 8A.3).
    """
    if not rows:
        return []
    out = [
        "",
        f"АВТОЗАКАЗ ОТОБРАН — {_items(len(rows))}. Это меняет поведение системы: "
        "черновики по этим позициям больше НЕ собираются сами.",
    ]
    for row in rows:
        drift = "" if row.drift_pct is None else f", расхождение {_pct(row.drift_pct)}"
        out.append(f"  – {row.name} ({_d(row.revoked_at)}{drift})")
        out.append(f"      {_REVOKE_CAUSE[row.cause]}")
    out.append(
        "Вернуть автозаказ можно только двумя чистыми пересчётами подряд — вручную "
        "включить его нельзя ни из бота, ни из командной строки (инвариант 2). "
        "Пока он выключен, заказывать по этим позициям нужно руками: /orders"
    )
    return out


def _digest_emergency(view: EmergencyDigestView | None) -> list[str]:
    """РОЗНИЧНАЯ НАЦЕНКА ЗА СПЕШКУ, с накопленной суммой. Spec 4.4.

    Эти строки не было видно нигде, кроме служебного отчёта: `build_split` считал
    экстренные позиции, отчёт по заказам их пересчитывал, а утренняя сводка молчала.
    Spec 4.4 говорит прямо: накопленный журнал походов в магазин — это и есть аргумент
    за то, чтобы поправить частоту заказов. Аргумент, который никто не читает, аргументом
    не является.

    Две суммы, и обе нужны. За последние недели — то, на что можно повлиять прямо сейчас.
    За всё время — то, чем спорят: один поход — плохая неделя, двадцать — сломанный
    график заказов.
    """
    if view is None or not view.any_runs:
        return []
    out = [
        "",
        "РОЗНИЧНАЯ НАЦЕНКА ЗА СПЕШКУ — "
        f"ВСЕГО {money(view.total_premium_pence)} за {_runs(view.total_runs)} "
        "в магазин.",
    ]
    if view.recent_runs:
        out.append(
            f"  за последние {_days(view.window_days)}: {_runs(view.recent_runs)} "
            f"на {money(view.recent_premium_pence)}"
        )
    else:
        out.append(
            f"  за последние {_days(view.window_days)} — ни одного похода. "
            "Сумма выше накопилась раньше."
        )
    for name, runs, premium in view.by_ingredient:
        out.append(f"  – {name}: {_runs(runs)}, {money(premium)}")
    if view.unpriced_runs:
        out.append(
            f"  {view.unpriced_runs} из {view.total_runs} без цены — в сумму не включены. "
            "Настоящая переплата больше указанной (неизвестная цена не равна нулю)."
        )
    out.append(
        "Это переплата за то, что товар понадобился раньше, чем приедет поставщик, "
        "а не за сам товар. Лечится не магазином, а частотой заказов и временем "
        "отсечения: /orders оформлять раньше."
    )
    return out


def _digest_counts(rows: Sequence[StockLineView], *, overdue_days: int) -> list[str]:
    if not rows:
        return [
            "",
            f"Пересчёты в порядке: ни одной позиции без пересчёта дольше {_days(overdue_days)}.",
        ]
    never = [row for row in rows if row.days_since_count is None]
    stale = [row for row in rows if row.days_since_count is not None]
    out = ["", f"ПОРА ПЕРЕСЧИТАТЬ — {_items(len(rows))}:"]
    if never:
        out.append(
            f"  не пересчитывалось ни разу: {len(never)} — "
            + ", ".join(row.name for row in never[:6])
            + (" …" if len(never) > 6 else "")
        )
        out.append(
            "  По этим позициям расчётный остаток — просто сумма движений, без опоры. "
            "Заказ по ним — догадка."
        )
    if stale:
        worst = sorted(stale, key=lambda r: -(r.days_since_count or 0))[:6]
        out.append(
            "  давно не пересчитывалось: "
            + ", ".join(f"{row.name} ({_days(row.days_since_count or 0)})" for row in worst)
            + (" …" if len(stale) > 6 else "")
        )
    out.append("/count — полный пересчёт, /count_a — быстрый по категории A.")
    return out


def digest(view: DigestView) -> str:
    rows = [
        f"УТРЕННЯЯ СВОДКА, {_d(view.local_date)}",
        f"отслеживается {_items(view.tracked_count)}. Все остатки ниже — РАСЧЁТНЫЕ: "
        "последний пересчёт плюс движения по журналу. Источник истины — пересчёт.",
    ]

    if view.negative:
        rows.append("")
        rows.append(
            f"ОТРИЦАТЕЛЬНЫЕ ОСТАТКИ — {_items(len(view.negative))}: "
            + ", ".join(row.name for row in view.negative[:8])
            + ". По журналу списано больше, чем было по пересчёту. Пересчитать."
        )

    if view.unanchored:
        rows.append("")
        rows.append(
            f"БЕЗ ЕДИНОГО ПЕРЕСЧЁТА — {_items(len(view.unanchored))}: "
            + ", ".join(row.name for row in view.unanchored[:8])
            + (" …" if len(view.unanchored) > 8 else "")
            + ". Это чистая сумма движений: на такие числа опираться при заказе нельзя."
        )

    rows += _digest_write_offs(view.pending_write_offs)
    if view.already_written_off:
        rows.append(
            f"(ранее уже списано партий: {view.already_written_off} — повторно они не считаются)"
        )
    rows += _digest_short_dated(view.short_dated)
    rows += _digest_orders(view.drafts)
    rows += _digest_deliveries(view.deliveries_expected)
    rows += _digest_drift(view.drift_alerts)
    rows += _digest_revocations(view.revocations)
    rows += _digest_emergency(view.emergency)
    rows += _digest_counts(view.counts_due, overdue_days=view.count_overdue_days)

    if view.checklist_low:
        rows.append("")
        rows.append(
            f"ПО ЧЕК-ЛИСТУ ЗАКАНЧИВАЕТСЯ — {_items(len(view.checklist_low))}: "
            + ", ".join(item.name for item in view.checklist_low[:10])
            + (" …" if len(view.checklist_low) > 10 else "")
            + ". Категория C не считается и не прогнозируется, поэтому количество "
            "называете вы: /checklist, отметить «заканчивается» и назвать число упаковок — "
            "строка встанет в черновик заказа этого поставщика с пометкой, что цифру "
            "выбрал человек. Без вашего подтверждения заказ всё равно не уйдёт."
        )
    if view.checklist_due:
        rows.append("")
        rows.append(
            f"ЧЕК-ЛИСТ КАТЕГОРИИ C — {_items(len(view.checklist_due))} не отмечено "
            "за неделю. /checklist"
        )

    if view.unbatched:
        rows.append("")
        rows.append(
            f"НЕ ПРИВЯЗАНО К ПАРТИЯМ — {_items(len(view.unbatched))}: "
            + ", ".join(row.name for row in view.unbatched[:6])
            + ". Эта часть остатка не может ни просрочиться, ни попасть в списание, "
            "потому что ни одна партия за неё не отвечает."
        )

    if view.headline:
        rows.append("")
        rows.append("КАТЕГОРИЯ A, расчётные остатки:")
        for line in view.headline:
            rows += _stock_row(line)

    if not view.telegram_configured:
        rows.append("")
        rows.append(
            "(Токен Telegram не задан: это сообщение никуда не отправлено, а выведено локально.)"
        )
    return "\n".join(rows)


# ==========================================================================
# Служебные сообщения и кнопки
# ==========================================================================

BTN_ORDER_PLUS = "+1"
BTN_ORDER_MINUS = "−1"
BTN_ORDER_CONFIRM = "Подтвердить заказ"
BTN_ORDER_CANCEL = "Не сейчас"


def start_text() -> str:
    return "\n".join(
        [
            "Учёт кофейни. Всё, что нужно делать руками, — здесь.",
            "",
            "/digest — утренняя сводка",
            "/orders — заказы, которые ждут подтверждения",
            "/count — полный пересчёт (раз в неделю)",
            "/count_a — быстрый пересчёт категории A (дважды в неделю)",
            "/checklist — чек-лист категории C: хватает или заканчивается",
            "/delivery — приёмка поставки со сроком годности",
            "",
            "/sale — продажа мимо кассы, Deliveroo или Just Eat",
            "/cash — наличные за день, одной суммой",
            "/export — транзакции за период файлом CSV",
            "/import — как прислать CSV: транзакции, выручка, отчёт площадки",
            "",
            "Без вашего подтверждения не заказывается ничего.",
        ]
    )


def help_text() -> str:
    return "\n".join(
        [
            "Коротко о числах.",
            "",
            "«Расчётный остаток» — последний фактический пересчёт плюс движения по "
            "журналу. Это НЕ пересчёт. Если пересчёта под числом нет, я это пишу "
            "прямо: доверять такому числу при заказе нельзя.",
            "",
            "«Прогноз не показан» означает, что прогноза нет — истории мало или её "
            "нет вовсе. В таких случаях я показываю причину ВМЕСТО цифры, чтобы "
            "не выдать догадку за расчёт.",
            "",
            "«Урезано намеренно» означает, что заказ меньше, чем просил прогноз, "
            "потому что остальное испортилось бы. Это не ошибка. Поднимать такое "
            "количество — значит покупать будущий убыток.",
            "",
            "«Добор» — строки, которые прогноз не просил: они добавлены только чтобы "
            "выйти на минимум поставщика. Они всегда названы по именам.",
            "",
            "«Из чек-листа» — строка, которую поставил человек по категории C. Там "
            "количество не считалось: категория C — это «хватает / заканчивается», "
            "без чисел, поэтому цифру называете вы, и строка об этом говорит прямо.",
            "",
            "«Автозаказ выключен» — это не примечание. Пока он выключен, черновики по "
            "позиции не собираются сами. Я всегда пишу, что именно его отобрало.",
            "",
            "«Розничная наценка за спешку» — сколько лишнего заплачено в магазине за то, "
            "что товар понадобился раньше поставки. Сумма накопительная, и она про "
            "частоту заказов, а не про магазин.",
            "",
            "«Мимо кассы» — продажа, которой касса не видела: наличные без чека, заказ "
            "Deliveroo или Just Eat. Кассу сюда вносить не надо, она приходит из "
            "Lightspeed сама. Каждая такая запись помечена, кто её внёс, и в кабинете "
            "отделяется от кассовых одним фильтром.",
        ]
    )


def err_bad_number(unit: Unit) -> str:
    return (
        f"Не понял число. Пришлите количество в {_UNITS[unit]}, например 3 или 3.5. "
        "Запятая тоже подойдёт."
    )


def err_bad_packs() -> str:
    return (
        "Нужно целое число упаковок — например 1 или 3.\n"
        "Дробную упаковку поставщик не отгрузит, а округлять за вас я не буду: "
        f"в заказ попало бы не то, что вы решили. «{BTN_CHECKLIST_NO_ORDER}» — "
        "если заказывать пока не нужно."
    )


def err_bad_date() -> str:
    return (
        "Не понял дату. Формат: ДД.ММ или ДД.ММ.ГГГГ, например 30.09 или 30.09.2026. "
        f"Если даты на упаковке нет — «{BTN_DELIVERY_NO_DATE}»."
    )


def err_not_adjustable(po_id: int) -> str:
    return (
        f"Заказ №{po_id} уже прошёл подтверждение — количества в нём менять нельзя. "
        "Это защита: иначе подтверждённый заказ мог бы тихо измениться под старой "
        "кнопкой."
    )


def err_order_all_zero(po_id: int) -> str:
    return (
        f"Заказ №{po_id} не подтверждён: во всех строках 0 упаковок — заказывать нечего. "
        f"Ничего не записано. Добавьте упаковки кнопкой «{BTN_ORDER_PLUS}» или оставьте "
        f"заказ черновиком — «{BTN_ORDER_CANCEL}»."
    )


def err_unknown() -> str:
    return "Не получилось. Ничего не записано — повторите действие или начните заново с /start."


# ==========================================================================
# Приёмка без заказа: поход в магазин
# ==========================================================================


def adhoc_usage() -> str:
    return (
        "Приёмка без заказа — это покупка, за которой не стоит ни один заказ "
        "(поход в магазин).\n"
        "Напишите так: /adhoc Whole milk\n"
        "Название должно совпадать однозначно: если под него подходят две позиции, "
        "я не угадываю — приёмка не на ту позицию потом не находится."
    )


def adhoc_not_found(name: str) -> str:
    return (
        f"Не нашёл однозначно позицию «{name}». Уточните название: я специально не "
        "выбираю похожее, потому что принятая не на ту позицию поставка искажает "
        "и остатки, и списания, и найти это потом нельзя."
    )


def adhoc_qty_prompt(ref: IngredientRefView) -> str:
    rows = [
        f"{ref.name} — приёмка без заказа.",
        f"хранение: {_STORAGE[ref.storage]}",
        "",
        f"Сколько принесли ({_UNITS[ref.unit]})?",
    ]
    return "\n".join(rows)


def adhoc_expiry_prompt() -> str:
    return (
        "КАКОЙ СРОК ГОДНОСТИ НА УПАКОВКЕ?\n"
        "Формат: ДД.ММ или ДД.ММ.ГГГГ.\n"
        "На розничной покупке дата есть почти всегда, и без неё эта партия не сможет "
        "ни просрочиться, ни попасть в списание — то есть убыток по ней окажется "
        "невидимым."
    )


# ==========================================================================
# Сообщения от расписания (планировщик пишет сам, ответить ему нельзя)
# ==========================================================================


def job_new_drafts(orders: Sequence[OrderView]) -> str:
    """Черновики, которые собрались по расписанию поставщика."""
    if not orders:
        return ""
    rows = [
        f"НОВЫЕ ЧЕРНОВИКИ ЗАКАЗА — {_items(len(orders))}. Ничего не заказано: "
        "каждый ждёт вашего подтверждения.",
    ]
    rows += _digest_orders(orders)[1:]
    return "\n".join(rows)


def job_drift_report(
    rows: Sequence[DriftAlertView], *, backfilled: int = 0, total_checked: int = 0
) -> str:
    """Недельный отчёт о расхождениях: не просто процент, а КАКАЯ это проблема."""
    if not rows:
        return job_nothing_to_report(total_checked=total_checked)
    head = [
        f"РАСХОЖДЕНИЯ ЗА НЕДЕЛЮ — {_items(len(rows))} из {total_checked} проверенных.",
        "Процент сам по себе ничего не говорит: у двух причин расхождения "
        "ПРОТИВОПОЛОЖНЫЕ решения, поэтому каждая строка названа по причине.",
    ]
    expiry = [row for row in rows if row.cause is DriftCause.EXPIRY]
    recipe = [row for row in rows if row.cause is DriftCause.MEASUREMENT]
    other = [row for row in rows if row.cause not in (DriftCause.EXPIRY, DriftCause.MEASUREMENT)]

    body: list[str] = []
    if expiry:
        body += ["", f"ЗАКУПАЕТСЯ ЛИШНЕЕ — {_items(len(expiry))}:"]
        body += [
            f"  – {row.name}: расхождение {_pct(row.drift_pct)}"
            + (
                ""
                if row.expiry_share is None
                else f", списаниями по сроку объясняется {row.expiry_share * 100:.0f}%"
            )
            for row in expiry
        ]
        body.append("Решение — заказывать меньше и чаще. Рецепт здесь менять не нужно.")
    if recipe:
        body += ["", f"ДЕЛО В РЕЦЕПТЕ ИЛИ В ПОТЕРЯХ — {_items(len(recipe))}:"]
        body += [f"  – {row.name}: расхождение {_pct(row.drift_pct)}" for row in recipe]
        body.append(
            "Решение — править рецепт или коэффициент потерь. Урезать заказ НЕЛЬЗЯ: "
            "будет дефицит, а расхождение останется."
        )
    if other:
        body += ["", f"ОСТАЛЬНОЕ — {_items(len(other))}:"]
        for row in other:
            line = f"  – {row.name}: расхождение {_pct(row.drift_pct)}"
            if row.verdict is not None:
                line += f", {_VERDICT[row.verdict]}"
            body.append(line)
            body.append(f"      {_GATE[row.gate_action]}")

    # Два уровня, а не один флаг. «Числам нельзя верить» и «автозаказ отобран» — разные
    # утверждения с разными ответами, и раньше отзыв в полосе 10–15% не говорил вообще
    # ничего (`ARCHITECTURE.md` 8A.3).
    alerts = [row for row in rows if row.alert_level is GateAlertLevel.ALARM]
    if alerts:
        body += [
            "",
            f"ТРЕВОГА — {_items(len(alerts))}: "
            + ", ".join(row.name for row in alerts)
            + ". По этим позициям расчётным остаткам доверять нельзя и автозаказ запрещён.",
        ]
    # The cause is captured alongside the row: a `not None` test inside a comprehension
    # does not narrow the attribute for the loop that indexes `_REVOKE_CAUSE` with it.
    revoked = [
        (row, cause)
        for row in rows
        if row.alert_level is GateAlertLevel.NOTICE and (cause := row.revoke_cause) is not None
    ]
    if revoked:
        body += ["", f"АВТОЗАКАЗ ОТОБРАН — {_items(len(revoked))}:"]
        for row, cause in revoked:
            body.append(f"  – {row.name}: {_REVOKE_CAUSE[cause]}")
        body.append(
            "Черновики по этим позициям больше не собираются сами. Расхождение при этом "
            "ещё в рабочей полосе — истекло не доверие к числам, а основание, на котором "
            "автозаказ был включён."
        )
    if backfilled:
        body += [
            "",
            f"(дополнительно посчитано расхождение по {backfilled} прошлым пересчётам, "
            "у которых его не было)",
        ]
    return "\n".join(head + body)


def job_nothing_to_report(*, total_checked: int = 0) -> str:
    return (
        f"Расхождения за неделю: ничего, что требует решения (проверено {_items(total_checked)})."
    )


def err_fractional_count(name: str) -> str:
    """Счётная позиция и дробное число. Отказ, а не округление.

    Округлить 9.5 до 10 значит записать в журнал не то, что человек видел на полке, и
    потом никто этого не различит. 9.5 стакана -- это опечатка, и правильный ответ на
    опечатку -- спросить снова.
    """
    return (
        f"«{name}» считается штуками, а 9.5 штуки не бывает. Пришлите целое число.\n"
        "Округлять за вас я не буду: в журнал попало бы не то, что вы видели на полке."
    )


def password_changed(at: datetime, actor: str | None, revoked: int) -> str:
    """Owner notice after the back-office web password was changed from Settings."""
    local = at.astimezone(_tz())
    who = f" ({actor})" if actor else ""
    sessions = _plural(revoked, "сеанс", "сеанса", "сеансов")
    return (
        f"Пароль веб-панели изменён в {local:%H:%M}, {_d(local)}{who}. "
        f"Завершено {revoked} {sessions} на других устройствах. "
        "Если это были не вы — сбросьте пароль на сервере: cafeops password reset."
    )


# ==========================================================================
# Карта лояльности (Sasha's Corner Rewards, docs/loyalty/CONTRACT.md §7):
# `/member`, сводка в 19:30, предупреждения о подозрительных штампах.
# ==========================================================================

_LOYALTY_ALERT_KINDS = {"stamp_rate": "много штампов за час"}
_LOYALTY_REWARDS = {"STAMP_CARD": "бесплатный напиток", "BIRTHDAY": "напиток на день рождения"}


def _loyalty_local(at: datetime) -> str:
    return at.astimezone(settings.tz).strftime("%d.%m %H:%M")


def alerts(rows: Sequence[AlertView]) -> str:
    lines = ["Карта лояльности: подозрительная активность"]
    for row in rows:
        kind = _LOYALTY_ALERT_KINDS.get(row.kind, row.kind)
        lines.append(f"- {_loyalty_local(row.at)} {kind}: {row.detail}")
    lines.append("Подробности: Members > Alerts в бэк-офисе.")
    return "\n".join(lines)


def daily_summary(s: DailySummary) -> str:
    return "\n".join(
        [
            f"Карта лояльности, {s.day.strftime('%d.%m.%Y')}",
            f"Новых участников: {s.new_members} (всего {s.members_total})",
            f"Визитов со штампом: {s.visits}, штампов: {s.stamps}",
            f"Выдано наград: {s.rewards_issued}, использовано: {s.redemptions}",
            *([f"Предупреждений: {s.alerts}"] if s.alerts else []),
        ]
    )


def member_usage() -> str:
    return "Напишите: /member <телефон или email>"


def member_not_found() -> str:
    return "Участник с таким телефоном или email не найден."


def member_brief(m: MemberBrief) -> str:
    rewards = ", ".join(_LOYALTY_REWARDS.get(k, k) for k in m.rewards) if m.rewards else "нет"
    last = _loyalty_local(m.last_visit) if m.last_visit else "ещё не было"
    return "\n".join(
        [
            f"{m.first_name} ({m.contact_masked})",
            f"Штампы: {m.stamps_current} из {m.stamps_required}",
            f"Награды: {rewards}",
            f"Последний визит: {last}",
            f"Участник с {m.member_since.strftime('%d.%m.%Y')}",
        ]
    )


# ==========================================================================
# Продажа мимо кассы, Deliveroo / Just Eat, наличные за день, файлы (DECISIONS 28)
# ==========================================================================

BTN_SALE_CASH = "Наличные (мимо кассы)"
BTN_SALE_DELIVEROO = "Deliveroo"
BTN_SALE_JUST_EAT = "Just Eat"
BTN_SALE_OTHER = "Другое"
BTN_SALE_BACK = "← Категории"
BTN_SALE_PREV = "← Назад"
BTN_SALE_NEXT = "Ещё →"
BTN_SALE_ADD = "Добавить позицию"
BTN_SALE_PRICE = "Другая цена"
BTN_SALE_DROP = "Убрать последнюю"
BTN_SALE_DATE = "Другая дата"
BTN_SALE_SAVE = "Записать"
BTN_SALE_CANCEL = "Отмена"
BTN_SALE_VOID = "Отменить эту продажу"

BTN_CASH_TODAY = "Сегодня"
BTN_CASH_YESTERDAY = "Вчера"
BTN_CASH_OTHER = "Другой день"
BTN_CASH_CANCEL = "Отмена"

BTN_EXPORT_TODAY = "Сегодня"
BTN_EXPORT_WEEK = "7 дней"
BTN_EXPORT_MONTH = "30 дней"
BTN_EXPORT_ALL = "Всё"

BTN_IMPORT_WRITE = "Записать"
BTN_IMPORT_CANCEL = "Отмена"
BTN_IMPORT_DELIVEROO = "Deliveroo"
BTN_IMPORT_JUST_EAT = "Just Eat"

_CHANNELS: dict[SaleChannel, str] = {
    SaleChannel.EPOS: "касса",
    SaleChannel.CASH: "наличные мимо кассы",
    SaleChannel.DELIVEROO: "Deliveroo",
    SaleChannel.JUST_EAT: "Just Eat",
    SaleChannel.OTHER: "другое",
    SaleChannel.WEB: "онлайн-заказы",
}

_CSV_KINDS: dict[CsvKind, str] = {
    CsvKind.TRANSACTIONS: "файл транзакций",
    CsvKind.PAYMENTS: "выгрузка выручки по дням",
    CsvKind.CHANNEL_REPORT: "отчёт площадки доставки",
    CsvKind.UNKNOWN: "неизвестный файл",
}


def _channel(channel: SaleChannel) -> str:
    return _CHANNELS.get(channel, channel.value)


def _day_label(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day:%d.%m}"


def _count(qty: Decimal) -> str:
    n = qty.normalize()
    return format(n, "f") if n != n.to_integral_value() else str(int(n))


def sale_channel_prompt() -> str:
    return "\n".join(
        [
            "Откуда продажа?",
            "",
            "Касса сюда не вносится — она приходит из Lightspeed сама, и повтор "
            "посчитался бы дважды. Здесь только то, чего касса не видела.",
        ]
    )


def sale_pick_prompt(basket: BasketView) -> str:
    lines = [f"{_channel(basket.channel).capitalize()}."]
    if basket.lines:
        lines.append(f"В чеке {_items(len(basket.lines))} на {money(basket.total_pence)}.")
    lines.append("Что продали? Выберите категорию или напишите часть названия.")
    return "\n".join(lines)


def sale_items_prompt(page: MenuPageView) -> str:
    head = page.category or "Без категории"
    if page.pages > 1:
        return f"{head} — страница {page.page + 1} из {page.pages}."
    return f"{head}."


def sale_search_results(q: str, found: int) -> str:
    if found == 0:
        return f"По «{q}» ничего не нашла. Напишите иначе или выберите категорию."
    return f"По «{q}» — {_items(found)}. Выберите:"


def sale_qty_prompt(item: MenuPickView) -> str:
    return f"{item.label} — {money(item.price_pence)}. Сколько штук?"


def _basket_lines(lines: Sequence[BasketLineView]) -> list[str]:
    out: list[str] = []
    for index, line in enumerate(lines, start=1):
        price = (
            f" по {money(line.unit_price_pence)}" if line.qty != 1 or line.price_is_custom else ""
        )
        custom = " (своя цена)" if line.price_is_custom else ""
        out.append(
            f"{index}. {line.label} × {_count(line.qty)}{price}{custom} — {money(line.gross_pence)}"
        )
    return out


def sale_basket(basket: BasketView) -> str:
    if not basket.lines:
        return "В чеке пока ничего нет. Выберите позицию."
    when = "сегодня" if basket.sold_on is None else _day_label(basket.sold_on)
    return "\n".join(
        [
            f"Чек — {_channel(basket.channel)}, {when}.",
            *_basket_lines(basket.lines),
            f"Итого: {money(basket.total_pence)}",
            "",
            "«Записать» внесёт продажу; со склада спишется ночью, как и кассовые.",
        ]
    )


def sale_price_prompt(line: BasketLineView) -> str:
    return "\n".join(
        [
            f"Цена за 1 шт. для «{line.label}»? Сейчас {money(line.unit_price_pence)}.",
            "Напишите сумму, например 4.50. Это цена площадки, меню не меняется.",
        ]
    )


def sale_date_prompt() -> str:
    return "\n".join(
        [
            "За какой день? Напишите ДД.ММ, например 27.09.",
            "Время у такой продажи будет полдень — важен день, не минута.",
        ]
    )


def sale_recorded(view: RecordedSaleView) -> str:
    return "\n".join(
        [
            f"Записано. {_channel(view.channel).capitalize()}, {_dt(view.sold_at)}.",
            *_basket_lines(view.lines),
            f"Итого: {money(view.total_pence)}",
            "",
            f"Чек {view.receipt_id}. Внёс: {view.recorded_by or '—'}.",
            "Со склада спишется ночью. Ошиблись — нажмите кнопку ниже.",
        ]
    )


def sale_voided(view: RecordedSaleView) -> str:
    return "\n".join(
        [
            f"Продажа {view.receipt_id} отменена ({money(view.total_pence)}).",
            "Строки остались в журнале с пометкой «отменено». Если списание уже "
            "прошло, остатки вернутся ночью.",
        ]
    )


def sale_cancelled() -> str:
    return "Отменено. Ничего не записано."


def sale_nothing_to_save() -> str:
    return "В чеке ничего нет — записывать нечего."


def sale_refused(reason: str) -> str:
    return f"Не записано: {reason}"


def err_bad_money() -> str:
    return "Не поняла сумму. Напишите число, например 12.50."


def err_bad_past_day() -> str:
    return "Не поняла дату. Напишите ДД.ММ, например 27.09 — сегодня или раньше."


def err_bad_count() -> str:
    return "Сколько штук? Напишите целое число, например 2."


# --- наличные за день -------------------------------------------------------


def cash_day_prompt() -> str:
    return "\n".join(
        [
            "Наличные за какой день?",
            "",
            "Одна сумма на день — всё, что взяли наличными (DECISIONS 26). "
            "Повторный ввод заменяет прежнюю сумму.",
        ]
    )


def cash_other_day_prompt() -> str:
    return "Напишите день: ДД.ММ, например 27.09."


def cash_amount_prompt(view: CashDayView) -> str:
    current = (
        "пока ничего не записано"
        if view.cash_pence is None
        else f"сейчас записано {money(view.cash_pence)}"
    )
    return "\n".join(
        [
            f"Сколько наличных за {_day_label(view.day)}? ({current})",
            "Напишите сумму, например 85.50.",
        ]
    )


def cash_locked(view: CashDayView) -> str:
    return "\n".join(
        [
            f"Наличные за {_day_label(view.day)} пришли из выгрузки ({view.locked_by}) "
            "и руками не меняются.",
            "Исправьте в источнике и пришлите выгрузку заново.",
        ]
    )


def cash_saved(view: CashDayView) -> str:
    card = (
        "карта за день не записана"
        if view.card_pence is None
        else f"карта за день {money(view.card_pence)}"
    )
    return f"Записано: наличные за {_day_label(view.day)} — {money(view.cash_pence)} ({card})."


def cash_cancelled() -> str:
    return "Отменено. Ничего не записано."


# --- файлы -------------------------------------------------------------------


def export_prompt() -> str:
    return "За какой период выгрузить транзакции? Придёт CSV — касса, наличные, доставка."


def export_caption(view: ExportView) -> str:
    if view.lines == 0:
        return f"Транзакций за {_d(view.since)}–{_d(view.until)} нет."
    voided = f", отменённых строк {view.voided_lines}" if view.voided_lines else ""
    return (
        f"Транзакции {_d(view.since)}–{_d(view.until)}: чеков {view.receipts}, "
        f"строк {view.lines}, на {money(view.gross_pence)}{voided}."
    )


def import_usage() -> str:
    return "\n".join(
        [
            "Пришлите CSV-файл сообщением — я разберу его по заголовкам и покажу, что "
            "запишется, прежде чем записать.",
            "",
            "Понимаю три вида файлов:",
            "• транзакции — колонки date, item, qty; по желанию size, unit price, "
            "channel (CASH / DELIVEROO / JUST_EAT / OTHER), receipt, time, note. "
            "Такой же файл делает /export, его можно поправить и прислать обратно;",
            "• выручка по дням — date, method, gross (выгрузка кассы);",
            "• отчёт Deliveroo или Just Eat из личного кабинета.",
            "",
            "Один и тот же файл дважды ничего не задвоит.",
        ]
    )


def err_not_csv() -> str:
    return "Нужен файл .csv. Из Excel или Numbers: «Экспорт» → CSV."


def import_platform_prompt(filename: str) -> str:
    return f"«{filename}» — колонки подходят и Deliveroo, и Just Eat. Чей это отчёт?"


def _import_body(view: ImportResultView) -> list[str]:
    out: list[str] = []
    when = f" за {_d(view.since)}–{_d(view.until)}" if view.since and view.until else ""
    if view.kind is CsvKind.TRANSACTIONS:
        out.append(
            f"Транзакции{when}: чеков {view.receipts}, строк {view.lines}, "
            f"на {money(view.gross_pence)}."
        )
        for channel, pence in view.by_channel:
            out.append(f"  {_channel(channel)}: {money(pence)}")
        if view.already_recorded:
            out.append(f"Уже были записаны раньше: {view.already_recorded} — пропущены.")
    elif view.kind is CsvKind.PAYMENTS:
        out.append(
            f"Выручка по дням{when}: новых дней {view.days_inserted}, "
            f"обновлено {view.days_updated}, всего {money(view.gross_pence)}."
        )
    elif view.kind is CsvKind.CHANNEL_REPORT:
        out.append(
            f"Отчёт {view.platform or '?'}{when}: дней +{view.days_inserted}/~{view.days_updated}, "
            f"позиций +{view.items_inserted}/~{view.items_updated}."
        )
    if view.rejected:
        out.append(f"Отклонено строк: {len(view.rejected)}. Первые:")
        out.extend(f"  · {reason}" for reason in view.rejected[:5])
        if len(view.rejected) > 5:
            out.append(f"  … и ещё {len(view.rejected) - 5}")
    for note in view.notes[:3]:
        out.append(f"  · {note}")
    return out


def import_preview(view: ImportResultView) -> str:
    if view.refused:
        return import_refused(view)
    return "\n".join(
        [
            f"«{view.filename}» — {_CSV_KINDS[view.kind]}. Пока ничего не записано.",
            *_import_body(view),
            "",
            "Записать?",
        ]
    )


def import_written(view: ImportResultView) -> str:
    if view.refused:
        return import_refused(view)
    return "\n".join([f"Записано: «{view.filename}».", *_import_body(view)])


def import_refused(view: ImportResultView) -> str:
    return "\n".join(
        [
            f"«{view.filename}» не принят, ничего не записано.",
            f"Причина: {view.refused}",
            "",
            "Нужны колонки: date, item, qty (транзакции); date, method, gross "
            "(выручка); либо отчёт Deliveroo / Just Eat как он выгружается.",
        ]
    )


def import_cancelled() -> str:
    return "Отменено. Файл не записан."


def import_nothing_pending() -> str:
    return "Нет файла, который ждал бы подтверждения. Пришлите CSV заново."
