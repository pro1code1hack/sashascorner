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
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    MediaAsset,
    MenuItem,
    Modifier,
    Sale,
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
from cafeops.services.shop import catalog, orders, payments, slots
from cafeops.services.shop.errors import ShopError

__all__ = [
    "LIVE_STATUSES",
    "POS_SINK_KEYS",
    "OrdersPage",
    "ProviderInfo",
    "ShopAdminRefused",
    "Summary",
    "banner_create",
    "banner_delete",
    "banner_update",
    "banners_order",
    "categories_order",
    "category_update",
    "delete_option_group",
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


#: "Live" on the board: placed and not yet collected, cancelled or rejected.
LIVE_STATUSES: tuple[OrderStatus, ...] = (
    OrderStatus.PENDING_PAYMENT,
    OrderStatus.NEW,
    OrderStatus.ACCEPTED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _now() -> datetime:
    return datetime.now(UTC)


def _staff(by: str) -> str:
    name = " ".join(by.split())[:60]
    if not name:
        raise ShopAdminRefused(422, "say who is doing this: the operator name is required")
    return f"staff:{name}"


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


def update_settings(session: Session, changes: Mapping[str, Any]) -> ShopSettings:
    """Apply a subset of §2.1's columns. Hours and closures are validated and replaced
    wholesale; every other key is set as given. Unknown keys are refused."""
    row = get_settings(session)
    for key, value in changes.items():
        if key == "hours":
            row.hours = _validate_hours(value)
        elif key == "closures":
            row.closures = _validate_closures(value)
        elif key == "default_dining":
            row.default_dining = DiningOption[str(value)]
        elif key == "payment_provider":
            chosen = str(value).strip().lower()
            if chosen not in registry.keys():
                raise ShopAdminRefused(
                    422,
                    f"'{value}' is not a payment provider; choose from "
                    + ", ".join(registry.keys()),
                )
            row.payment_provider = chosen
        elif key == "pos_sink":
            chosen = str(value).strip().lower()
            if chosen not in POS_SINK_KEYS:
                raise ShopAdminRefused(
                    422, f"'{value}' is not a POS sink; choose from " + ", ".join(POS_SINK_KEYS)
                )
            row.pos_sink = chosen
        elif key in _SETTINGS_FIELDS:
            setattr(row, key, value)
        else:
            raise ShopAdminRefused(422, f"'{key}' is not a shop setting")
    if not row.takeaway_enabled and not row.eat_in_enabled:
        raise ShopAdminRefused(
            422, "at least one of takeaway and eat in must stay on, or nobody can order"
        )
    if not row.pay_at_counter and not row.pay_online:
        raise ShopAdminRefused(
            422, "at least one way to pay must stay on: at the counter or online"
        )
    row.updated_at = _now()
    session.commit()
    return row


_SETTINGS_FIELDS = frozenset(
    {
        "email_notify",
        "push_notify",
        "sms_notify",
        "enabled",
        "closed_message",
        "hero_title",
        "hero_subtitle",
        "takeaway_enabled",
        "eat_in_enabled",
        "lead_minutes",
        "slot_minutes",
        "max_orders_per_slot",
        "days_ahead",
        "last_order_minutes_before_close",
        "pay_at_counter",
        "pay_online",
        "kcal_notice",
        "allergen_notice",
        "collection_note",
        "terms_url",
        "loyalty_stamps_online",
        "notify_telegram",
    }
)


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
    tz = settings.tz
    today = datetime.now(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz).astimezone(UTC)
    return start, start + timedelta(days=1)


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
        open_now=slots.open_now(row, _now()),
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
        session.rollback()
        raise ShopAdminRefused(exc.status, exc.detail) from exc
    session.commit()
    session.refresh(order)
    return order


def allowed_transitions(order: ShopOrder) -> list[OrderStatus]:
    order_of = list(OrderStatus)
    allowed = TRANSITIONS.get(order.status.name, frozenset())
    return [s for s in order_of if s.name in allowed]


def set_staff_note(session: Session, order_id: int, note: str | None, *, by: str) -> ShopOrder:
    order = order_or_404(session, order_id)
    orders.add_staff_note(session, order, note or "", actor=_staff(by))
    session.commit()
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
        session.rollback()
        raise ShopAdminRefused(exc.status, exc.detail) from exc
    session.commit()
    return order


def refund(session: Session, order_id: int, *, by: str, reason: str | None) -> ShopOrder:
    """Refund a PAID ONLINE order through the payment provider (CONTRACT §10.F).

    A COUNTER payment is refused with "refund it from the till" (409); so is an order
    that is not PAID. When the provider says no, its sentence comes back as the 409
    AFTER the `refund_failed` event is committed, so the timeline shows the attempt.
    """
    order = order_or_404(session, order_id)
    actor = _staff(by)
    try:
        result = payments.refund_order(session, order, actor=actor, reason=reason)
    except ShopError as exc:
        session.rollback()
        raise ShopAdminRefused(exc.status, exc.detail) from exc
    session.commit()
    if not result.ok:
        raise ShopAdminRefused(409, result.detail)
    session.refresh(order)
    return order


def sales_for(session: Session, order: ShopOrder) -> list[Sale]:
    if order.sale_receipt_id is None:
        return []
    return list(
        session.scalars(
            select(Sale)
            .where(Sale.lightspeed_receipt_id == order.sale_receipt_id)
            .order_by(Sale.id)
        )
    )


# ==========================================================================
# Catalogue
# ==========================================================================


def sync_catalogue(session: Session) -> None:
    catalog.sync_categories(session)
    catalog.sync_products(session)
    session.commit()


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
    row.updated_at = _now()
    session.commit()
    return row


def categories_order(session: Session, ids: Sequence[int]) -> None:
    _reorder(session.scalars(select(ShopCategory)), ids, "shop category")
    session.commit()


def set_category_photo(session: Session, category_id: int, asset_id: int | None) -> ShopCategory:
    row = category_or_404(session, category_id)
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    row.updated_at = _now()
    session.commit()
    return row


# --- products ---------------------------------------------------------------


def product_or_404(session: Session, product_id: int) -> ShopProduct:
    row = session.get(ShopProduct, product_id)
    if row is None:
        raise ShopAdminRefused(404, f"shop product {product_id} does not exist")
    return row


def product_for_menu_item(session: Session, menu_item_id: int) -> ShopProduct:
    """The shop product behind an ops menu item (by name). Syncs first when missing; a
    name the sync skips (no active size) gets a row here so the editor still works.
    404 only when the menu item itself does not exist."""
    item = session.get(MenuItem, menu_item_id)
    if item is None:
        raise ShopAdminRefused(404, f"menu item {menu_item_id} does not exist")
    row = session.scalar(select(ShopProduct).where(ShopProduct.item_name == item.name))
    if row is None:
        catalog.sync_products(session)
        session.flush()
        row = session.scalar(select(ShopProduct).where(ShopProduct.item_name == item.name))
    if row is None:
        row = ShopProduct(item_name=item.name, category_ops_name=item.category, updated_at=_now())
        session.add(row)
        session.flush()
    session.commit()
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


def product_update(session: Session, product_id: int, changes: Mapping[str, Any]) -> ShopProduct:
    row = product_or_404(session, product_id)
    for key, value in changes.items():
        if key in _PRODUCT_TEXT:
            setattr(
                row,
                key,
                (" ".join(str(value).split())[: _PRODUCT_TEXT[key]] or None)
                if value is not None
                else None,
            )
        elif key == "allergens":
            cleaned = _clean_list(value, ALLERGENS, "allergen")
            problem = allergens_problem(cleaned)
            if problem is not None:
                raise ShopAdminRefused(422, problem)
            row.allergens = cleaned
        elif key == "dietary":
            row.dietary = _clean_list(value, DIETARY, "dietary tag")
        elif key == "kcal_by_size":
            row.kcal_by_size = (
                {str(k): int(v) for k, v in dict(value).items()} if value is not None else None
            )
        elif key == "nutrition":
            row.nutrition = (
                {str(k): str(v) for k, v in dict(value).items() if str(v).strip()}
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
            row.category_ops_name = value
        elif key == "default_size":
            row.default_size = value
        elif key == "option_group_ids":
            _set_product_groups(session, row, [int(i) for i in value])
        elif key in ("kcal", "visible", "available", "featured", "sort_order"):
            setattr(row, key, value)
        else:
            raise ShopAdminRefused(422, f"'{key}' is not an editable product field")
    row.updated_at = _now()
    session.commit()
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
    session.commit()


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
    now = _now()
    for row in rows:
        if available is not None:
            row.available = available
        if visible is not None:
            row.visible = visible
        row.updated_at = now
    session.commit()
    return rows


def set_product_photo(session: Session, product_id: int, asset_id: int | None) -> ShopProduct:
    row = product_or_404(session, product_id)
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    row.updated_at = _now()
    session.commit()
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
    row.updated_at = _now()
    session.add(row)
    session.flush()
    _apply_options(session, row, body.get("options", ()))
    session.commit()
    return row


def option_group_update(
    session: Session, group_id: int, body: Mapping[str, Any]
) -> ShopOptionGroup:
    row = group_or_404(session, group_id)
    _apply_group_fields(session, row, body)
    _apply_options(session, row, body.get("options", ()))
    row.updated_at = _now()
    session.commit()
    return row


def delete_option_group(session: Session, group_id: int) -> None:
    row = group_or_404(session, group_id)
    session.delete(row)  # options and attachments cascade (§2.5)
    session.commit()


def option_groups_order(session: Session, ids: Sequence[int]) -> None:
    _reorder(session.scalars(select(ShopOptionGroup)), ids, "option group")
    session.commit()


def set_option_photo(session: Session, option_id: int, asset_id: int | None) -> ShopOption:
    row = session.get(ShopOption, option_id)
    if row is None:
        raise ShopAdminRefused(404, f"option {option_id} does not exist")
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    session.commit()
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
    row.placement = UpsellPlacement[str(body["placement"])]
    row.heading = " ".join(str(body["heading"]).split())[:80]
    row.product_ids = _check_products(session, body.get("product_ids", ()))
    row.sort_order = int(body.get("sort_order", 0))
    row.active = bool(body.get("active", True))


def upsell_create(session: Session, body: Mapping[str, Any]) -> ShopUpsell:
    row = ShopUpsell()
    _apply_upsell(session, row, body)
    session.add(row)
    session.commit()
    return row


def upsell_update(session: Session, upsell_id: int, body: Mapping[str, Any]) -> ShopUpsell:
    row = session.get(ShopUpsell, upsell_id)
    if row is None:
        raise ShopAdminRefused(404, f"upsell {upsell_id} does not exist")
    _apply_upsell(session, row, body)
    session.commit()
    return row


def upsell_delete(session: Session, upsell_id: int) -> None:
    row = session.get(ShopUpsell, upsell_id)
    if row is None:
        raise ShopAdminRefused(404, f"upsell {upsell_id} does not exist")
    session.delete(row)
    session.commit()


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
    session.commit()
    return row


def banner_update(session: Session, banner_id: int, body: Mapping[str, Any]) -> ShopBanner:
    row = banner_or_404(session, banner_id)
    _apply_banner(row, body)
    session.commit()
    return row


def banner_delete(session: Session, banner_id: int) -> None:
    session.delete(banner_or_404(session, banner_id))
    session.commit()


def banners_order(session: Session, ids: Sequence[int]) -> None:
    _reorder(session.scalars(select(ShopBanner)), ids, "banner")
    session.commit()


def set_banner_photo(session: Session, banner_id: int, asset_id: int | None) -> ShopBanner:
    row = banner_or_404(session, banner_id)
    _asset_or_404(session, asset_id)
    row.photo_asset_id = asset_id
    session.commit()
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
