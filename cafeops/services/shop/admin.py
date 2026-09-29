"""The back office's side of online ordering: settings, catalogue editing, order handling.

`docs/shop/CONTRACT.md` §5. Every write the admin API makes lands here; the views in
`api/areas/shop_admin_views.py` only convert. Status changes and "collected" (which
writes the sale, the stamps and the reward redemption, §3.1 and §3.5) are NOT
re-implemented: they go through `services/shop/orders.py`, so the customer's cancel
and the staff's cancel obey one set of transition rules (§3.8).

Refusals are `ShopAdminRefused(status, detail)` -- a sentence and the HTTP status it
deserves (404 missing, 409 the world moved, 422 cannot be honoured as asked). The
views turn them into `HTTPException` so the screen shows the sentence verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.clock import local_day_bounds, local_today, utcnow
from cafeops.config import settings
from cafeops.db.models import (
    MediaAsset,
    MenuItem,
    Modifier,
)
from cafeops.db.models.shop import (
    DiningOption,
    OptionKind,
    OptionLayout,
    OrderStatus,
    PaymentStatus,
    ShopBanner,
    ShopCategory,
    ShopOption,
    ShopOptionGroup,
    ShopOrder,
    ShopProduct,
    ShopProductOptionGroup,
    ShopSettings,
    ShopUpsell,
    UpsellPlacement,
)
from cafeops.domain.shop import ALLERGENS, DIETARY, TRANSITIONS, allergens_problem
from cafeops.integrations.payments import registry
from cafeops.integrations.pos.lightspeed import LightspeedSink
from cafeops.services.actor import require_actor
from cafeops.services.finance.common import UNSET, Unset
from cafeops.services.shop import catalog, orders, payments, slots
from cafeops.services.shop.errors import ShopError

__all__ = [
    "LIVE_STATUSES",
    "POS_SINK_KEYS",
    "OrdersPage",
    "ProductChange",
    "ProviderInfo",
    "RefundDeclined",
    "SettingsChange",
    "ShopAdminRefused",
    "Summary",
    "banner_create",
    "banner_delete",
    "banner_update",
    "banners_order",
    "categories_order",
    "category_update",
    "delete_option_group",
    "ensure_product_for_menu_item",
    "get_settings",
    "mark_paid",
    "option_group_create",
    "option_group_update",
    "option_groups_order",
    "order_or_404",
    "orders_page",
    "payment_providers",
    "pos_sinks",
    "product_for_menu_item",
    "product_update",
    "products_bulk",
    "products_order",
    "refund",
    "set_banner_photo",
    "set_category_photo",
    "set_option_photo",
    "set_product_photo",
    "set_staff_note",
    "set_status",
    "summary",
    "sync_catalogue",
    "update_settings",
    "upsell_create",
    "upsell_delete",
    "upsell_update",
]


class ShopAdminRefused(Exception):
    """A refusal with the HTTP status it deserves and a sentence for the screen."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class RefundDeclined(ShopAdminRefused):
    """The payment provider refused a refund. Unlike every other refusal this one is
    also a record: the `refund_failed` event is flushed and the caller must COMMIT it
    before answering 409, or the attempt vanishes from the order's timeline."""

    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


#: "Live" on the board: placed and not yet collected, cancelled or rejected.
LIVE_STATUSES: tuple[OrderStatus, ...] = (
    OrderStatus.PENDING_PAYMENT,
    OrderStatus.NEW,
    OrderStatus.ACCEPTED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _ActorRefused(ShopAdminRefused):
    """`require_actor`'s refusal, as the 422 the admin API has always answered."""

    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


_STAFF = "staff:"
#: `shop_order_event.actor` is String(80); the prefix eats into it.
_STAFF_NAME_MAX = 80 - len(_STAFF)


def _staff(by: str) -> str:
    return _STAFF + require_actor(by, error=_ActorRefused)[:_STAFF_NAME_MAX]


# ==========================================================================
# Settings
# ==========================================================================


def get_settings(session: Session) -> ShopSettings:
    row = session.get(ShopSettings, 1)
    if row is None:
        # The migration creates row 1; a database that predates it still deserves a
        # working screen. Defaults are the model's.
        row = ShopSettings(id=1)
        session.add(row)
        session.flush()
    return row


def _validate_hours(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for entry in raw:
        weekday = int(entry["weekday"])
        open_ = str(entry["open"])
        close = str(entry["close"])
        if weekday < 0 or weekday > 6:
            raise ShopAdminRefused(422, f"weekday {weekday} is not in 0 (Monday) to 6 (Sunday)")
        if weekday in seen:
            raise ShopAdminRefused(
                422, f"weekday {weekday} appears twice; give each day one open and close"
            )
        seen.add(weekday)
        for label, value in (("open", open_), ("close", close)):
            if not _HHMM.match(value):
                raise ShopAdminRefused(
                    422, f"the {label} time '{value}' is not HH:MM (24-hour, like 09:00)"
                )
        if open_ >= close:
            raise ShopAdminRefused(
                422, f"on weekday {weekday} the shop closes ({close}) before it opens ({open_})"
            )
        out.append({"weekday": weekday, "open": open_, "close": close})
    out.sort(key=lambda h: int(h["weekday"]))
    return out


def _validate_closures(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        value = entry["date"]
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
        iso = day.isoformat()
        if iso in seen:
            raise ShopAdminRefused(422, f"{iso} is listed twice in closures")
        seen.add(iso)
        note = entry.get("note")
        out.append({"date": iso, "note": (str(note).strip()[:120] or None) if note else None})
    out.sort(key=lambda c: str(c["date"]))
    return out


@dataclass(frozen=True, slots=True)
class SettingsChange:
    """A PATCH of §2.1's columns: a field left `UNSET` was not sent and is kept.

    Every field that was sent is validated -- and the "someone must still be able to
    order and pay" rules checked against the result -- BEFORE the row is touched, so a
    refused change leaves the settings exactly as they were. Hours and closures are
    replaced wholesale."""

    enabled: bool | Unset | None = UNSET
    closed_message: str | Unset | None = UNSET
    hero_title: str | Unset | None = UNSET
    hero_subtitle: str | Unset | None = UNSET
    takeaway_enabled: bool | Unset | None = UNSET
    eat_in_enabled: bool | Unset | None = UNSET
    default_dining: str | Unset | None = UNSET
    lead_minutes: int | Unset | None = UNSET
    slot_minutes: int | Unset | None = UNSET
    max_orders_per_slot: int | Unset | None = UNSET
    days_ahead: int | Unset | None = UNSET
    hours: Sequence[Mapping[str, Any]] | Unset | None = UNSET
    last_order_minutes_before_close: int | Unset | None = UNSET
    closures: Sequence[Mapping[str, Any]] | Unset | None = UNSET
    pay_at_counter: bool | Unset | None = UNSET
    pay_online: bool | Unset | None = UNSET
    kcal_notice: str | Unset | None = UNSET
    allergen_notice: str | Unset | None = UNSET
    collection_note: str | Unset | None = UNSET
    terms_url: str | Unset | None = UNSET
    loyalty_stamps_online: bool | Unset | None = UNSET
    notify_telegram: bool | Unset | None = UNSET
    guest_orders: bool | Unset | None = UNSET
    payment_provider: str | Unset | None = UNSET
    pos_sink: str | Unset | None = UNSET
    email_notify: bool | Unset | None = UNSET
    push_notify: bool | Unset | None = UNSET
    sms_notify: str | Unset | None = UNSET


def _sent(change: SettingsChange | ProductChange) -> dict[str, object]:
    """The fields of a change dataclass that were sent, by name."""
    return {
        f.name: getattr(change, f.name)
        for f in fields(change)
        if not isinstance(getattr(change, f.name), Unset)
    }


def update_settings(session: Session, change: SettingsChange) -> ShopSettings:
    """Apply a `SettingsChange`. Validates everything first; mutates only when all of
    it is acceptable."""
    row = get_settings(session)
    planned: dict[str, object] = {}
    for key, value in _sent(change).items():
        if value is None:
            # Every settings column is NOT NULL: an explicit null is a mistake, not "clear".
            raise ShopAdminRefused(422, f"'{key}' cannot be empty; send a value or leave it out")
        if key == "hours":
            planned[key] = _validate_hours(cast(Sequence[Mapping[str, Any]], value))
        elif key == "closures":
            planned[key] = _validate_closures(cast(Sequence[Mapping[str, Any]], value))
        elif key == "default_dining":
            try:
                planned[key] = DiningOption[str(value)]
            except KeyError:
                raise ShopAdminRefused(
                    422, f"'{value}' is not a dining option; choose TAKEAWAY or EAT_IN"
                ) from None
        elif key == "payment_provider":
            chosen = str(value).strip().lower()
            if chosen not in registry.keys():
                raise ShopAdminRefused(
                    422,
                    f"'{value}' is not a payment provider; choose from "
                    + ", ".join(registry.keys()),
                )
            planned[key] = chosen
        elif key == "pos_sink":
            chosen = str(value).strip().lower()
            if chosen not in POS_SINK_KEYS:
                raise ShopAdminRefused(
                    422, f"'{value}' is not a POS sink; choose from " + ", ".join(POS_SINK_KEYS)
                )
            planned[key] = chosen
        else:
            planned[key] = value

    def after(key: str) -> object:
        return planned[key] if key in planned else getattr(row, key)

    if not after("takeaway_enabled") and not after("eat_in_enabled"):
        raise ShopAdminRefused(
            422, "at least one of takeaway and eat in must stay on, or nobody can order"
        )
    if not after("pay_at_counter") and not after("pay_online"):
        raise ShopAdminRefused(
            422, "at least one way to pay must stay on: at the counter or online"
        )
    for key, value in planned.items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.flush()
    return row


def stripe_configured() -> bool:
    return bool(settings.stripe_secret_key)


def smtp_configured() -> bool:
    return settings.smtp_configured


def sms_configured() -> bool:
    return settings.twilio_configured


def push_configured() -> bool:
    return settings.push_configured


def telegram_configured() -> bool:
    return bool(settings.telegram_bot_token) and settings.telegram_owner_chat_id is not None


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    key: str
    display_name: str
    configured: bool


#: CONTRACT §3b: where a NEW order is pushed. "none" is always configured; "lightspeed"
#: answers through the sink's own `configured()`. TODO(agent B): read the keys from
#: `integrations/pos/registry` once Agent A adds it (its package docstring promises
#: `registry.sink()`); until then the key list lives here.
POS_SINK_KEYS: tuple[str, ...] = ("none", "lightspeed")


def payment_providers() -> list[ProviderInfo]:
    return [
        ProviderInfo(key=p["key"], display_name=p["display_name"], configured=p["configured"])
        for p in registry.available()
    ]


def pos_sinks() -> list[ProviderInfo]:
    lightspeed = LightspeedSink()
    return [
        ProviderInfo(key="none", display_name="None (record the sale here only)", configured=True),
        ProviderInfo(
            key="lightspeed",
            display_name=lightspeed.display_name,
            configured=lightspeed.configured(),
        ),
    ]


# ==========================================================================
# Summary and orders
# ==========================================================================


@dataclass(frozen=True, slots=True)
class Summary:
    enabled: bool
    open_now: bool
    new: int
    accepted: int
    preparing: int
    ready: int
    today_collected: int
    today_cancelled: int
    today_revenue_pence: int
    next_due: list[ShopOrder]


def _today_window() -> tuple[datetime, datetime]:
    """Today's local day as a UTC window -- via the one helper that gets DST days right."""
    return local_day_bounds(local_today(settings.tz), tz=settings.tz)


def summary(session: Session) -> Summary:
    row = get_settings(session)
    counts: dict[OrderStatus, int] = {}
    for status, n in session.execute(
        select(ShopOrder.status, func.count()).group_by(ShopOrder.status)
    ):
        counts[status] = int(n)
    start, end = _today_window()
    today_collected = (
        session.scalar(
            select(func.count())
            .select_from(ShopOrder)
            .where(ShopOrder.status == OrderStatus.COLLECTED)
            .where(ShopOrder.collected_at >= start, ShopOrder.collected_at < end)
        )
        or 0
    )
    today_cancelled = (
        session.scalar(
            select(func.count())
            .select_from(ShopOrder)
            .where(ShopOrder.status.in_((OrderStatus.CANCELLED, OrderStatus.REJECTED)))
            .where(ShopOrder.cancelled_at >= start, ShopOrder.cancelled_at < end)
        )
        or 0
    )
    revenue = (
        session.scalar(
            select(func.coalesce(func.sum(ShopOrder.total_pence), 0))
            .where(ShopOrder.status == OrderStatus.COLLECTED)
            .where(ShopOrder.collected_at >= start, ShopOrder.collected_at < end)
        )
        or 0
    )
    next_due = list(
        session.scalars(
            select(ShopOrder)
            .where(ShopOrder.status.in_(LIVE_STATUSES[1:]))
            .order_by(ShopOrder.requested_at.asc(), ShopOrder.id.asc())
            .limit(8)
        )
    )
    return Summary(
        enabled=row.enabled,
        open_now=slots.open_now(row, utcnow()),
        new=counts.get(OrderStatus.NEW, 0),
        accepted=counts.get(OrderStatus.ACCEPTED, 0),
        preparing=counts.get(OrderStatus.PREPARING, 0),
        ready=counts.get(OrderStatus.READY, 0),
        today_collected=int(today_collected),
        today_cancelled=int(today_cancelled),
        today_revenue_pence=int(revenue),
        next_due=next_due,
    )


@dataclass(frozen=True, slots=True)
class OrdersPage:
    items: list[ShopOrder]
    total: int
    page: int
    page_size: int


def orders_page(
    session: Session,
    *,
    status: str,
    since: date | None,
    until: date | None,
    q: str | None,
    page: int,
    page_size: int,
) -> OrdersPage:
    stmt = select(ShopOrder)
    if status == "live":
        stmt = stmt.where(ShopOrder.status.in_(LIVE_STATUSES))
    elif status == "today":
        start, end = _today_window()
        stmt = stmt.where(ShopOrder.requested_at >= start, ShopOrder.requested_at < end)
    elif status != "all":
        try:
            stmt = stmt.where(ShopOrder.status == OrderStatus[status.upper()])
        except KeyError:
            raise ShopAdminRefused(
                422, f"'{status}' is not a status filter: live, today, all or a status name"
            ) from None
    tz = settings.tz
    if since is not None:
        stmt = stmt.where(
            ShopOrder.placed_at >= datetime.combine(since, time.min, tzinfo=tz).astimezone(UTC)
        )
    if until is not None:
        stmt = stmt.where(
            ShopOrder.placed_at
            < datetime.combine(until + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
        )
    if q:
        needle = f"%{q.strip()}%"
        code = q.strip().upper().removeprefix("SC-")
        stmt = stmt.where(
            ShopOrder.code.ilike(f"%{code}%")
            | ShopOrder.customer_name.ilike(needle)
            | ShopOrder.customer_email.ilike(needle)
            | ShopOrder.customer_phone.ilike(needle)
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if status == "live":
        stmt = stmt.order_by(ShopOrder.requested_at.asc(), ShopOrder.id.asc())
    else:
        stmt = stmt.order_by(ShopOrder.placed_at.desc(), ShopOrder.id.desc())
    items = list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return OrdersPage(items=items, total=int(total), page=page, page_size=page_size)


def order_or_404(session: Session, order_id: int) -> ShopOrder:
    row = session.get(ShopOrder, order_id)
    if row is None:
        raise ShopAdminRefused(404, f"order {order_id} does not exist")
    return row


def set_status(
    session: Session, order_id: int, status_name: str, *, reason: str | None, by: str
) -> ShopOrder:
    """One transition, through `services/shop/orders` (§3.8). 409 with a sentence when
    the move is not allowed from where the order is."""
    order = order_or_404(session, order_id)
    try:
        target = OrderStatus[status_name]
    except KeyError:
        raise ShopAdminRefused(422, f"'{status_name}' is not an order status") from None
    actor = _staff(by)
    try:
        orders.transition(session, order, target, actor=actor, reason=reason)
    except ShopError as exc:
        raise ShopAdminRefused(exc.status, exc.detail) from exc
    session.flush()
    session.refresh(order)
    return order


def allowed_transitions(order: ShopOrder) -> list[OrderStatus]:
    order_of = list(OrderStatus)
    allowed = TRANSITIONS.get(order.status.name, frozenset())
    return [s for s in order_of if s.name in allowed]


def set_staff_note(session: Session, order_id: int, note: str | None, *, by: str) -> ShopOrder:
    order = order_or_404(session, order_id)
    orders.add_staff_note(session, order, note or "", actor=_staff(by))
    session.flush()
    return order


def mark_paid(session: Session, order_id: int, *, by: str) -> ShopOrder:
    """A COUNTER order paid before collection. COLLECTED already implies paid (§3.6)."""
    order = order_or_404(session, order_id)
    actor = _staff(by)
    if order.payment_status == PaymentStatus.PAID:
        raise ShopAdminRefused(409, f"order SC-{order.code} is already marked paid")
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
        raise ShopAdminRefused(
            409, f"order SC-{order.code} was {order.status.name.lower()}; nothing to pay for"
        )
    if order.status == OrderStatus.PENDING_PAYMENT:
        raise ShopAdminRefused(
            409,
            f"order SC-{order.code} is waiting for its online payment; "
            "Stripe confirms that, not the counter",
        )
    try:
        orders.mark_paid(session, order, actor=actor)
    except ShopError as exc:
        raise ShopAdminRefused(exc.status, exc.detail) from exc
    session.flush()
    return order


def refund(session: Session, order_id: int, *, by: str, reason: str | None) -> ShopOrder:
    """Refund a PAID ONLINE order through the payment provider (CONTRACT §10.F).

    A COUNTER payment is refused with "refund it from the till" (409); so is an order
    that is not PAID. When the provider says no, its sentence comes back as
    `RefundDeclined` (409) with the `refund_failed` event already flushed: the caller
    commits before answering, so the timeline shows the attempt.
    """
    order = order_or_404(session, order_id)
    actor = _staff(by)
    try:
        result = payments.refund_order(session, order, actor=actor, reason=reason)
    except ShopError as exc:
        raise ShopAdminRefused(exc.status, exc.detail) from exc
    session.flush()
    if not result.ok:
        raise RefundDeclined(result.detail)
    session.refresh(order)
    return order


# ==========================================================================
# Catalogue
# ==========================================================================


def sync_catalogue(session: Session) -> None:
    catalog.sync_categories(session)
    catalog.sync_products(session)
    session.flush()


def _asset_or_404(session: Session, asset_id: int | None) -> None:
    if asset_id is not None and session.get(MediaAsset, asset_id) is None:
        raise ShopAdminRefused(404, f"media asset {asset_id} not found")


def _reorder(rows: Iterable[Any], ids: Sequence[int], what: str) -> None:
    by_id = {row.id: row for row in rows}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ShopAdminRefused(404, f"{what} {missing[0]} does not exist")
    if len(set(ids)) != len(ids):
        raise ShopAdminRefused(422, f"the same {what} appears twice in the order")
    position = {i: n for n, i in enumerate(ids)}
    rest = sorted(
        (r for r in by_id.values() if r.id not in position), key=lambda r: (r.sort_order, r.id)
    )
    for n, row in enumerate(rest, start=len(ids)):
        position[row.id] = n
    for row in by_id.values():
        row.sort_order = position[row.id]


# --- categories -------------------------------------------------------------


def category_or_404(session: Session, category_id: int) -> ShopCategory:
    row = session.get(ShopCategory, category_id)
    if row is None:
        raise ShopAdminRefused(404, f"shop category {category_id} does not exist")
    return row


def category_update(session: Session, category_id: int, changes: Mapping[str, Any]) -> ShopCategory:
    row = category_or_404(session, category_id)
    for key, value in changes.items():
        if key == "name":
            row.name = " ".join(str(value).split())[:80] or row.ops_name
        elif key == "blurb":
            row.blurb = (str(value).strip()[:300] or None) if value is not None else None
        elif key in ("visible", "sort_order"):
            setattr(row, key, value)
        else:
            raise ShopAdminRefused(422, f"'{key}' is not an editable category field")
    row.updated_at = utcnow()
    session.flush()
    return row


def categories_order(session: Session, ids: Sequence[int]) -> None:
    _reorder(session.scalars(select(ShopCategory)), ids, "shop category")
    session.flush()


def set_category_photo(session: Session, category_id: int, asset_id: int | None) -> ShopCategory:
    row = category_or_404(session, category_id)
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    row.updated_at = utcnow()
    session.flush()
    return row


# --- products ---------------------------------------------------------------


def product_or_404(session: Session, product_id: int) -> ShopProduct:
    row = session.get(ShopProduct, product_id)
    if row is None:
        raise ShopAdminRefused(404, f"shop product {product_id} does not exist")
    return row


def product_for_menu_item(session: Session, menu_item_id: int) -> ShopProduct:
    """The shop product behind an ops menu item (by name). READ-ONLY: a GET answers
    with this, so a product not listed yet comes back as an unsaved default -- the row
    `catalog.sync_products` would create -- never added to the session (its `id` is
    None). `ensure_product_for_menu_item` is the writing twin. 404 only when the menu
    item itself does not exist."""
    item = session.get(MenuItem, menu_item_id)
    if item is None:
        raise ShopAdminRefused(404, f"menu item {menu_item_id} does not exist")
    row = session.scalar(select(ShopProduct).where(ShopProduct.item_name == item.name))
    if row is not None:
        return row
    last = session.scalar(select(func.max(ShopProduct.sort_order)))
    return ShopProduct(
        item_name=item.name,
        category_ops_name=(item.category or "").strip() or None,
        allergens=[],
        dietary=[],
        sort_order=(last or 0) + 10,
        visible=True,
        available=True,
        featured=False,
    )


def ensure_product_for_menu_item(session: Session, menu_item_id: int) -> ShopProduct:
    """`product_for_menu_item`, persisted: syncs the catalogue when the product is
    missing, and a name the sync skips (no active size) gets a row here so the editor
    still works. Only an edit (PUT) calls this."""
    found = product_for_menu_item(session, menu_item_id)
    if found.id is not None:
        return found
    catalog.sync_products(session)
    session.flush()
    row = session.scalar(select(ShopProduct).where(ShopProduct.item_name == found.item_name))
    if row is None:
        item = session.get(MenuItem, menu_item_id)
        assert item is not None  # product_for_menu_item 404s first
        row = ShopProduct(item_name=item.name, category_ops_name=item.category, updated_at=utcnow())
        session.add(row)
        session.flush()
    return row


def _clean_list(values: Iterable[str], vocabulary: Sequence[str], what: str) -> list[str]:
    out: list[str] = []
    for v in values:
        key = str(v).strip().lower()
        if key not in vocabulary:
            raise ShopAdminRefused(
                422, f"'{v}' is not a known {what}; choose from {', '.join(vocabulary)}"
            )
        if key not in out:
            out.append(key)
    return out


_PRODUCT_TEXT: dict[str, int] = {
    "display_name": 200,
    "description": 600,
    "note": 300,
    "ingredients_text": 600,
    "badge": 30,
}


@dataclass(frozen=True, slots=True)
class ProductChange:
    """A PATCH of one shop product: a field left `UNSET` was not sent and is kept;
    `None` clears a nullable field. Validated in full before the row is touched."""

    display_name: str | Unset | None = UNSET
    description: str | Unset | None = UNSET
    note: str | Unset | None = UNSET
    ingredients_text: str | Unset | None = UNSET
    badge: str | Unset | None = UNSET
    kcal: int | Unset | None = UNSET
    kcal_by_size: Mapping[str, int] | Unset | None = UNSET
    nutrition: Mapping[str, str] | Unset | None = UNSET
    allergens: Sequence[str] | Unset | None = UNSET
    dietary: Sequence[str] | Unset | None = UNSET
    visible: bool | Unset | None = UNSET
    available: bool | Unset | None = UNSET
    featured: bool | Unset | None = UNSET
    category_ops_name: str | Unset | None = UNSET
    sort_order: int | Unset | None = UNSET
    default_size: str | Unset | None = UNSET
    option_group_ids: Sequence[int] | Unset | None = UNSET


#: NOT NULL product columns: an explicit null is refused rather than left to the database.
_PRODUCT_REQUIRED = frozenset(
    {"allergens", "dietary", "visible", "available", "featured", "sort_order", "option_group_ids"}
)


def product_update(session: Session, product_id: int, change: ProductChange) -> ShopProduct:
    """Apply a `ProductChange`. Every sent field is validated first; nothing is written
    unless all of it is acceptable."""
    row = product_or_404(session, product_id)
    planned: dict[str, object] = {}
    groups: list[int] | None = None
    for key, value in _sent(change).items():
        if value is None and key in _PRODUCT_REQUIRED:
            raise ShopAdminRefused(422, f"'{key}' cannot be empty; send a value or leave it out")
        if key in _PRODUCT_TEXT:
            planned[key] = (
                (" ".join(str(value).split())[: _PRODUCT_TEXT[key]] or None)
                if value is not None
                else None
            )
        elif key == "allergens":
            cleaned = _clean_list(cast(Sequence[str], value), ALLERGENS, "allergen")
            problem = allergens_problem(cleaned)
            if problem is not None:
                raise ShopAdminRefused(422, problem)
            planned[key] = cleaned
        elif key == "dietary":
            planned[key] = _clean_list(cast(Sequence[str], value), DIETARY, "dietary tag")
        elif key == "kcal_by_size":
            planned[key] = (
                {str(k): int(v) for k, v in cast(Mapping[str, int], value).items()}
                if value is not None
                else None
            )
        elif key == "nutrition":
            planned[key] = (
                {
                    str(k): str(v)
                    for k, v in cast(Mapping[str, str], value).items()
                    if str(v).strip()
                }
                if value is not None
                else None
            )
        elif key == "category_ops_name":
            if value is not None:
                exists = session.scalar(
                    select(func.count())
                    .select_from(ShopCategory)
                    .where(ShopCategory.ops_name == value)
                )
                if not exists:
                    raise ShopAdminRefused(404, f"there is no category called '{value}'")
            planned[key] = value
        elif key == "option_group_ids":
            groups = [int(i) for i in cast(Sequence[int], value)]
            known = set(session.scalars(select(ShopOptionGroup.id)))
            for gid in groups:
                if gid not in known:
                    raise ShopAdminRefused(404, f"option group {gid} does not exist")
        else:  # kcal, visible, available, featured, sort_order, default_size
            planned[key] = value
    for key, value in planned.items():
        setattr(row, key, value)
    if groups is not None:
        _set_product_groups(session, row, groups)
    row.updated_at = utcnow()
    session.flush()
    return row


def _set_product_groups(session: Session, product: ShopProduct, group_ids: Sequence[int]) -> None:
    known = {g.id for g in session.scalars(select(ShopOptionGroup))}
    for gid in group_ids:
        if gid not in known:
            raise ShopAdminRefused(404, f"option group {gid} does not exist")
    existing = {
        link.group_id: link
        for link in session.scalars(
            select(ShopProductOptionGroup).where(ShopProductOptionGroup.product_id == product.id)
        )
    }
    for gid, stale in existing.items():
        if gid not in group_ids:
            session.delete(stale)
    for n, gid in enumerate(group_ids):
        link = existing.get(gid)
        if link is None:
            session.add(ShopProductOptionGroup(product_id=product.id, group_id=gid, sort_order=n))
        else:
            link.sort_order = n
    session.flush()


def products_order(session: Session, category_slug: str, ids: Sequence[int]) -> None:
    category = session.scalar(select(ShopCategory).where(ShopCategory.slug == category_slug))
    if category is None:
        raise ShopAdminRefused(404, f"there is no shop category with slug '{category_slug}'")
    rows = list(
        session.scalars(
            select(ShopProduct).where(ShopProduct.category_ops_name == category.ops_name)
        )
    )
    _reorder(rows, ids, "shop product")
    session.flush()


def products_bulk(
    session: Session, ids: Sequence[int], *, available: bool | None, visible: bool | None
) -> list[ShopProduct]:
    if available is None and visible is None:
        raise ShopAdminRefused(422, "say what to change: available and/or visible")
    rows = list(session.scalars(select(ShopProduct).where(ShopProduct.id.in_(list(ids)))))
    found = {r.id for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ShopAdminRefused(404, f"shop product {missing[0]} does not exist")
    now = utcnow()
    for row in rows:
        if available is not None:
            row.available = available
        if visible is not None:
            row.visible = visible
        row.updated_at = now
    session.flush()
    return rows


def set_product_photo(session: Session, product_id: int, asset_id: int | None) -> ShopProduct:
    row = product_or_404(session, product_id)
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    row.updated_at = utcnow()
    session.flush()
    return row


# --- option groups ------------------------------------------------------------


def group_or_404(session: Session, group_id: int) -> ShopOptionGroup:
    row = session.get(ShopOptionGroup, group_id)
    if row is None:
        raise ShopAdminRefused(404, f"option group {group_id} does not exist")
    return row


def _apply_group_fields(session: Session, row: ShopOptionGroup, body: Mapping[str, Any]) -> None:
    row.name = " ".join(str(body["name"]).split())[:80]
    prompt = body.get("prompt")
    row.prompt = (" ".join(str(prompt).split())[:160] or None) if prompt else None
    row.kind = OptionKind[str(body["kind"])]
    row.layout = OptionLayout[str(body["layout"])]
    row.required = bool(body.get("required", False))
    row.min_select = int(body.get("min_select", 0))
    max_select = body.get("max_select")
    row.max_select = int(max_select) if max_select is not None else None
    row.collapsed = bool(body.get("collapsed", False))
    row.active = bool(body.get("active", True))
    if body.get("sort_order") is not None:
        row.sort_order = int(body["sort_order"])
    slugs = {c.slug for c in session.scalars(select(ShopCategory))}
    applies: list[str] = []
    for slug in body.get("applies_to_categories", ()):
        if slug not in slugs:
            raise ShopAdminRefused(404, f"there is no shop category with slug '{slug}'")
        if slug not in applies:
            applies.append(slug)
    row.applies_to_categories = applies
    if row.kind == OptionKind.SINGLE:
        row.max_select = 1
        if row.required and row.min_select < 1:
            row.min_select = 1
    if row.max_select is not None and row.max_select < row.min_select:
        raise ShopAdminRefused(
            422, f"max_select ({row.max_select}) is below min_select ({row.min_select})"
        )
    if row.required and row.min_select < 1:
        row.min_select = 1


def _apply_options(
    session: Session, group: ShopOptionGroup, raw: Sequence[Mapping[str, Any]]
) -> None:
    """Replace the group's options wholesale; an entry with `id` updates that option
    (keeping its photo), one without creates. Options not mentioned are deleted."""
    existing = {
        o.id: o for o in session.scalars(select(ShopOption).where(ShopOption.group_id == group.id))
    }
    modifiers = set(session.scalars(select(Modifier.id))) if raw else set()
    keep: set[int] = set()
    defaults = 0
    # A client that sends no sort_order at all (every entry 0) means "in this order".
    positional = all(int(entry.get("sort_order", 0) or 0) == 0 for entry in raw)
    for n, entry in enumerate(raw):
        oid = entry.get("id")
        if oid is not None:
            option = existing.get(int(oid))
            if option is None:
                raise ShopAdminRefused(404, f"option {oid} is not in group '{group.name}'")
        else:
            option = ShopOption(group_id=group.id)
            session.add(option)
        option.name = " ".join(str(entry["name"]).split())[:80]
        desc = entry.get("description")
        option.description = (" ".join(str(desc).split())[:120] or None) if desc else None
        option.price_delta_pence = int(entry.get("price_delta_pence", 0))
        kcal = entry.get("kcal")
        option.kcal = int(kcal) if kcal is not None else None
        option.is_default = bool(entry.get("is_default", False))
        option.available = bool(entry.get("available", True))
        option.sort_order = n if positional else int(entry.get("sort_order", n))
        modifier_id = entry.get("modifier_id")
        if modifier_id is not None and int(modifier_id) not in modifiers:
            raise ShopAdminRefused(404, f"modifier {modifier_id} does not exist")
        option.modifier_id = int(modifier_id) if modifier_id is not None else None
        if option.is_default:
            defaults += 1
        session.flush()
        keep.add(option.id)
    if group.kind == OptionKind.SINGLE and defaults > 1:
        raise ShopAdminRefused(
            422, f"'{group.name}' is a single-choice group; only one option can be the default"
        )
    for oid, option in existing.items():
        if oid not in keep:
            session.delete(option)
    session.flush()


def option_group_create(session: Session, body: Mapping[str, Any]) -> ShopOptionGroup:
    row = ShopOptionGroup()
    _apply_group_fields(session, row, body)
    if body.get("sort_order") is None:
        last = session.scalar(select(func.max(ShopOptionGroup.sort_order)))
        row.sort_order = (last if last is not None else -1) + 1
    row.updated_at = utcnow()
    session.add(row)
    session.flush()
    _apply_options(session, row, body.get("options", ()))
    session.flush()
    return row


def option_group_update(
    session: Session, group_id: int, body: Mapping[str, Any]
) -> ShopOptionGroup:
    row = group_or_404(session, group_id)
    _apply_group_fields(session, row, body)
    _apply_options(session, row, body.get("options", ()))
    row.updated_at = utcnow()
    session.flush()
    return row


def delete_option_group(session: Session, group_id: int) -> None:
    row = group_or_404(session, group_id)
    session.delete(row)  # options and attachments cascade (§2.5)
    session.flush()


def option_groups_order(session: Session, ids: Sequence[int]) -> None:
    _reorder(session.scalars(select(ShopOptionGroup)), ids, "option group")
    session.flush()


def set_option_photo(session: Session, option_id: int, asset_id: int | None) -> ShopOption:
    row = session.get(ShopOption, option_id)
    if row is None:
        raise ShopAdminRefused(404, f"option {option_id} does not exist")
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    session.flush()
    return row


# --- upsells ------------------------------------------------------------------


def _check_products(session: Session, ids: Sequence[int]) -> list[int]:
    known = set(session.scalars(select(ShopProduct.id)))
    out: list[int] = []
    for pid in ids:
        if int(pid) not in known:
            raise ShopAdminRefused(404, f"shop product {pid} does not exist")
        if int(pid) not in out:
            out.append(int(pid))
    return out


def _apply_upsell(session: Session, row: ShopUpsell, body: Mapping[str, Any]) -> None:
    """Create takes the whole shape; an update may send any subset (`None` = keep)."""
    if body.get("placement") is not None:
        row.placement = UpsellPlacement[str(body["placement"])]
    if body.get("heading") is not None:
        row.heading = " ".join(str(body["heading"]).split())[:80]
    if body.get("product_ids") is not None:
        row.product_ids = _check_products(session, body["product_ids"])
    elif row.product_ids is None:
        row.product_ids = []
    if body.get("sort_order") is not None:
        row.sort_order = int(body["sort_order"])
    elif row.sort_order is None:
        row.sort_order = 0
    if body.get("active") is not None:
        row.active = bool(body["active"])
    elif row.active is None:
        row.active = True


def upsell_create(session: Session, body: Mapping[str, Any]) -> ShopUpsell:
    row = ShopUpsell()
    _apply_upsell(session, row, body)
    session.add(row)
    session.flush()
    return row


def upsell_update(session: Session, upsell_id: int, body: Mapping[str, Any]) -> ShopUpsell:
    row = session.get(ShopUpsell, upsell_id)
    if row is None:
        raise ShopAdminRefused(404, f"upsell {upsell_id} does not exist")
    _apply_upsell(session, row, body)
    session.flush()
    return row


def upsell_delete(session: Session, upsell_id: int) -> None:
    row = session.get(ShopUpsell, upsell_id)
    if row is None:
        raise ShopAdminRefused(404, f"upsell {upsell_id} does not exist")
    session.delete(row)
    session.flush()


# --- banners ------------------------------------------------------------------


def banner_or_404(session: Session, banner_id: int) -> ShopBanner:
    row = session.get(ShopBanner, banner_id)
    if row is None:
        raise ShopAdminRefused(404, f"banner {banner_id} does not exist")
    return row


def _apply_banner(row: ShopBanner, body: Mapping[str, Any]) -> None:
    row.title = " ".join(str(body["title"]).split())[:120]
    subtitle = body.get("subtitle")
    row.subtitle = (" ".join(str(subtitle).split())[:300] or None) if subtitle else None
    link = body.get("link_href")
    row.link_href = (str(link).strip()[:300] or None) if link else None
    if row.link_href is not None and not (
        row.link_href.startswith("/") or row.link_href.startswith("https://")
    ):
        raise ShopAdminRefused(
            422, "a banner link is a site path like /order/c/hot-drinks or an https:// address"
        )
    row.active = bool(body.get("active", True))
    row.sort_order = int(body.get("sort_order", 0))
    starts = body.get("starts_on")
    ends = body.get("ends_on")
    row.starts_on = (
        starts if isinstance(starts, date) or starts is None else date.fromisoformat(str(starts))
    )
    row.ends_on = ends if isinstance(ends, date) or ends is None else date.fromisoformat(str(ends))
    if row.starts_on is not None and row.ends_on is not None and row.ends_on < row.starts_on:
        raise ShopAdminRefused(422, "the banner ends before it starts")


def banner_create(session: Session, body: Mapping[str, Any]) -> ShopBanner:
    row = ShopBanner()
    _apply_banner(row, body)
    session.add(row)
    session.flush()
    return row


def banner_update(session: Session, banner_id: int, body: Mapping[str, Any]) -> ShopBanner:
    row = banner_or_404(session, banner_id)
    _apply_banner(row, body)
    session.flush()
    return row


def banner_delete(session: Session, banner_id: int) -> None:
    session.delete(banner_or_404(session, banner_id))
    session.flush()


def banners_order(session: Session, ids: Sequence[int]) -> None:
    _reorder(session.scalars(select(ShopBanner)), ids, "banner")
    session.flush()


def set_banner_photo(session: Session, banner_id: int, asset_id: int | None) -> ShopBanner:
    row = banner_or_404(session, banner_id)
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    session.flush()
    return row


# --- modifiers ----------------------------------------------------------------


def modifiers(session: Session) -> list[Modifier]:
    return list(
        session.scalars(
            select(Modifier).where(Modifier.is_active.is_(True)).order_by(Modifier.name)
        )
    )


def menu_items_active(session: Session) -> int:
    return int(
        session.scalar(select(func.count()).select_from(MenuItem).where(MenuItem.active.is_(True)))
        or 0
    )
