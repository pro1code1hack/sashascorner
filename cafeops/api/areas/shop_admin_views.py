"""Views for the Online orders area. Reads here; writes delegate to `services/shop/admin`.

Everything is built inside the worker thread (`runtime.in_session`) so a closed
session never bites (see `api/runtime.py`). Every refusal from the service is a
`ShopAdminRefused` and becomes an `HTTPException` with the same sentence, so the
screen shows it verbatim (FRONTEND-KIT §1 rule 10).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from cafeops.api.areas.shop_admin_schemas import (
    BannerAdminOut,
    BannerIn,
    BulkOut,
    CatalogueAdminOut,
    CategoryAdminOut,
    CategoryIn,
    ClosureOut,
    DeletedOut,
    EffectiveGroupOut,
    HoursRowOut,
    InsightsDayOut,
    InsightsDiningOut,
    InsightsFiguresOut,
    InsightsHourOut,
    InsightsOptionOut,
    InsightsPaymentOut,
    InsightsPeriodOut,
    InsightsPreviousOut,
    InsightsProductOut,
    InsightsStatusNowOut,
    InsightsVsPreviousOut,
    InsightsWeekdayOut,
    LineOptionOut,
    ModifierOut,
    NextDueOut,
    OpsOut,
    OptionAdminOut,
    OptionGroupAdminOut,
    OptionGroupIn,
    OrderAdminOut,
    OrderEventOut,
    OrderLineOut,
    OrderMemberOut,
    OrdersPageOut,
    PhotoOut,
    ProductAdminOut,
    ProductByMenuItemOut,
    ProductIn,
    ProductsBulkIn,
    ProductSizeOut,
    ProviderOut,
    ShopInsightsOut,
    ShopSettingsIn,
    ShopSettingsOut,
    SummaryCountsOut,
    SummaryOut,
    UpsellAdminOut,
    UpsellIn,
)
from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyMember,
    MediaAsset,
    MenuItem,
    Modifier,
    ShopBanner,
    ShopCategory,
    ShopOption,
    ShopOptionGroup,
    ShopOrder,
    ShopProduct,
    ShopProductOptionGroup,
    ShopSettings,
    ShopUpsell,
    SizeCode,
)
from cafeops.domain.shop import ALLERGENS, DIETARY, allergens_state
from cafeops.services.media_store import MediaRefusedError, media_url, store_image
from cafeops.services.shop import admin, insights, payments
from cafeops.services.shop.admin import ShopAdminRefused
from cafeops.services.shop.catalog import product_slug

_SIZE_LABELS: dict[SizeCode, str] = {
    SizeCode.S: "Small",
    SizeCode.M: "Medium",
    SizeCode.XL: "Large",
    SizeCode.ONE: "",
}
_SIZE_SORT: dict[SizeCode, int] = {SizeCode.S: 0, SizeCode.M: 1, SizeCode.XL: 2, SizeCode.ONE: 3}


def _refused(exc: ShopAdminRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def guarded[T](work: Callable[[], T]) -> T:
    """Run a service call, translating its refusals into HTTP answers."""
    try:
        return work()
    except ShopAdminRefused as exc:
        raise _refused(exc) from exc
    except MediaRefusedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ==========================================================================
# Time formatting
# ==========================================================================


def _local(at: datetime, *, now: datetime | None = None) -> str:
    """ "Today 14:20", "Tomorrow 09:10", "Tue 30 Sep 14:20" in Europe/London."""
    tz = settings.tz
    local = at.astimezone(tz)
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    day = local.date()
    clock = local.strftime("%H:%M")
    if day == today:
        return f"Today {clock}"
    if day == today + timedelta(days=1):
        return f"Tomorrow {clock}"
    if day == today - timedelta(days=1):
        return f"Yesterday {clock}"
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')} {clock}"


def _minutes_until(at: datetime, now: datetime) -> int:
    return int((at - now).total_seconds() // 60)


# ==========================================================================
# Orders
# ==========================================================================


def _members(session: Session, orders: Iterable[ShopOrder]) -> dict[int, OrderMemberOut]:
    ids = {o.member_id for o in orders if o.member_id is not None}
    if not ids:
        return {}
    out: dict[int, OrderMemberOut] = {}
    for member in session.scalars(select(LoyaltyMember).where(LoyaltyMember.id.in_(ids))):
        out[member.id] = OrderMemberOut(
            id=member.id, first_name=member.first_name, stamps_current=0
        )
    stamps: dict[int, int] = {}
    for _card_id, member_id, current in session.execute(
        select(LoyaltyCard.id, LoyaltyCard.member_id, LoyaltyCard.stamps_current).where(
            LoyaltyCard.member_id.in_(ids), LoyaltyCard.voided_at.is_(None)
        )
    ):
        stamps[member_id] = max(stamps.get(member_id, 0), int(current))
    return {
        mid: OrderMemberOut(id=m.id, first_name=m.first_name, stamps_current=stamps.get(mid, 0))
        for mid, m in out.items()
    }


def _order_out(
    order: ShopOrder, members: dict[int, OrderMemberOut], now: datetime
) -> OrderAdminOut:
    return OrderAdminOut(
        id=order.id,
        code=order.code,
        code_display=f"SC-{order.code}",
        status=cast(Any, order.status.name),
        dining=cast(Any, order.dining.name),
        asap=order.asap,
        requested_at=order.requested_at,
        requested_local=_local(order.requested_at, now=now),
        placed_at=order.placed_at,
        placed_local=_local(order.placed_at, now=now),
        minutes_until_due=_minutes_until(order.requested_at, now),
        accepted_at=order.accepted_at,
        ready_at=order.ready_at,
        collected_at=order.collected_at,
        cancelled_at=order.cancelled_at,
        cancel_reason=order.cancel_reason,
        cancelled_by=order.cancelled_by,
        customer_name=order.customer_name,
        customer_phone=order.customer_phone,
        customer_email=order.customer_email,
        member_id=order.member_id,
        card_id=order.card_id,
        member=members.get(order.member_id) if order.member_id is not None else None,
        note=order.note,
        table=order.table,
        staff_note=order.staff_note,
        allergy_ack=order.allergy_ack,
        subtotal_pence=order.subtotal_pence,
        discount_pence=order.discount_pence,
        total_pence=order.total_pence,
        reward_id=order.reward_id,
        payment_method=cast(Any, order.payment_method.name),
        payment_status=cast(Any, order.payment_status.name),
        payment_ref=order.payment_ref,
        payment_intent=order.payment_intent,
        refundable=payments.refundable(order),
        paid_at=order.paid_at,
        sale_receipt_id=order.sale_receipt_id,
        stamp_event_id=order.stamp_event_id,
        pos_ref=order.pos_ref,
        client_ip_hash=order.client_ip_hash,
        user_agent=order.user_agent,
        updated_at=order.updated_at,
        lines=tuple(
            OrderLineOut(
                id=line.id,
                product_id=line.product_id,
                menu_item_id=line.menu_item_id,
                name=line.name,
                size_label=line.size_label,
                qty=line.qty,
                unit_price_pence=line.unit_price_pence,
                options=tuple(
                    LineOptionOut(
                        group=str(o.get("group", "")),
                        name=str(o.get("name", "")),
                        price_delta_pence=int(o.get("price_delta_pence", 0) or 0),
                        modifier_id=(
                            int(o["modifier_id"]) if o.get("modifier_id") is not None else None
                        ),
                    )
                    for o in line.options
                ),
                line_total_pence=line.line_total_pence,
                sort_order=line.sort_order,
            )
            for line in order.lines
        ),
        events=tuple(
            OrderEventOut(id=e.id, at=e.at, kind=e.kind, detail=e.detail, actor=e.actor)
            for e in order.events
        ),
        allowed_transitions=tuple(cast(Any, s.name) for s in admin.allowed_transitions(order)),
    )


def _one_order(session: Session, order: ShopOrder) -> OrderAdminOut:
    session.refresh(order, attribute_names=["lines", "events"])
    return _order_out(order, _members(session, [order]), datetime.now(UTC))


def summary_view(session: Session) -> SummaryOut:
    s = admin.summary(session)
    now = datetime.now(UTC)
    return SummaryOut(
        enabled=s.enabled,
        open_now=s.open_now,
        counts=SummaryCountsOut(
            new=s.new,
            accepted=s.accepted,
            preparing=s.preparing,
            ready=s.ready,
            today_collected=s.today_collected,
            today_cancelled=s.today_cancelled,
        ),
        today_revenue_pence=s.today_revenue_pence,
        next_due=tuple(
            NextDueOut(
                id=o.id,
                code=f"SC-{o.code}",
                requested_local=_local(o.requested_at, now=now),
                customer_name=o.customer_name,
                status=cast(Any, o.status.name),
            )
            for o in s.next_due
        ),
        stripe_configured=admin.stripe_configured(),
        telegram_configured=admin.telegram_configured(),
        smtp_configured=admin.smtp_configured(),
        sms_configured=admin.sms_configured(),
        push_configured=admin.push_configured(),
        payment_providers=tuple(_provider_out(p) for p in admin.payment_providers()),
        pos_sinks=tuple(_provider_out(p) for p in admin.pos_sinks()),
    )


def _provider_out(p: admin.ProviderInfo) -> ProviderOut:
    return ProviderOut(key=p.key, display_name=p.display_name, configured=p.configured)


def orders_view(
    session: Session,
    *,
    status: str,
    since: Any,
    until: Any,
    q: str | None,
    page: int,
    page_size: int,
) -> OrdersPageOut:
    result = guarded(
        lambda: admin.orders_page(
            session, status=status, since=since, until=until, q=q, page=page, page_size=page_size
        )
    )
    ids = [o.id for o in result.items]
    if ids:
        # One round trip for lines and events rather than two per row.
        session.execute(
            select(ShopOrder)
            .where(ShopOrder.id.in_(ids))
            .options(selectinload(ShopOrder.lines), selectinload(ShopOrder.events))
        )
    members = _members(session, result.items)
    now = datetime.now(UTC)
    return OrdersPageOut(
        items=tuple(_order_out(o, members, now) for o in result.items),
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


def order_view(session: Session, order_id: int) -> OrderAdminOut:
    order = guarded(lambda: admin.order_or_404(session, order_id))
    return _order_out(order, _members(session, [order]), datetime.now(UTC))


def status_view(
    session: Session, order_id: int, *, status: str, reason: str | None, by: str
) -> OrderAdminOut:
    order = guarded(lambda: admin.set_status(session, order_id, status, reason=reason, by=by))
    return _one_order(session, order)


def note_view(session: Session, order_id: int, *, staff_note: str | None, by: str) -> OrderAdminOut:
    order = guarded(lambda: admin.set_staff_note(session, order_id, staff_note, by=by))
    return _one_order(session, order)


def paid_view(session: Session, order_id: int, *, by: str) -> OrderAdminOut:
    order = guarded(lambda: admin.mark_paid(session, order_id, by=by))
    return _one_order(session, order)


def refund_view(session: Session, order_id: int, *, by: str, reason: str | None) -> OrderAdminOut:
    order = guarded(lambda: admin.refund(session, order_id, by=by, reason=reason))
    return _one_order(session, order)


# ==========================================================================
# Settings
# ==========================================================================


def _settings_out(row: ShopSettings) -> ShopSettingsOut:
    return ShopSettingsOut(
        enabled=row.enabled,
        closed_message=row.closed_message,
        hero_title=row.hero_title,
        hero_subtitle=row.hero_subtitle,
        takeaway_enabled=row.takeaway_enabled,
        eat_in_enabled=row.eat_in_enabled,
        default_dining=cast(Any, row.default_dining.name),
        lead_minutes=row.lead_minutes,
        slot_minutes=row.slot_minutes,
        max_orders_per_slot=row.max_orders_per_slot,
        days_ahead=row.days_ahead,
        hours=tuple(
            HoursRowOut(weekday=int(h["weekday"]), open=str(h["open"]), close=str(h["close"]))
            for h in row.hours
        ),
        last_order_minutes_before_close=row.last_order_minutes_before_close,
        closures=tuple(ClosureOut(date=c["date"], note=c.get("note")) for c in row.closures),
        pay_at_counter=row.pay_at_counter,
        pay_online=row.pay_online,
        kcal_notice=row.kcal_notice,
        allergen_notice=row.allergen_notice,
        collection_note=row.collection_note,
        terms_url=row.terms_url,
        loyalty_stamps_online=row.loyalty_stamps_online,
        notify_telegram=row.notify_telegram,
        payment_provider=row.payment_provider,
        pos_sink=row.pos_sink,
        email_notify=row.email_notify,
        push_notify=row.push_notify,
        sms_notify=cast(Any, row.sms_notify),
        updated_at=row.updated_at,
        stripe_configured=admin.stripe_configured(),
        telegram_configured=admin.telegram_configured(),
        smtp_configured=admin.smtp_configured(),
        sms_configured=admin.sms_configured(),
        push_configured=admin.push_configured(),
    )


def settings_view(session: Session) -> ShopSettingsOut:
    return _settings_out(admin.get_settings(session))


def settings_update_view(session: Session, body: ShopSettingsIn) -> ShopSettingsOut:
    changes = body.model_dump(exclude_unset=True)
    row = guarded(lambda: admin.update_settings(session, changes))
    return _settings_out(row)


# ==========================================================================
# Catalogue
# ==========================================================================


def _photos(session: Session, ids: Iterable[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {
        a.id: media_url(a.filename)
        for a in session.scalars(select(MediaAsset).where(MediaAsset.id.in_(wanted)))
    }


def _category_out(
    row: ShopCategory, photos: dict[int, str], product_counts: dict[str, int]
) -> CategoryAdminOut:
    return CategoryAdminOut(
        id=row.id,
        ops_name=row.ops_name,
        name=row.name,
        slug=row.slug,
        blurb=row.blurb,
        photo_url=photos.get(row.photo_asset_id) if row.photo_asset_id is not None else None,
        sort_order=row.sort_order,
        visible=row.visible,
        product_count=product_counts.get(row.ops_name, 0),
        updated_at=row.updated_at,
    )


def _sizes_by_name(session: Session) -> dict[str, list[MenuItem]]:
    out: dict[str, list[MenuItem]] = defaultdict(list)
    for item in session.scalars(select(MenuItem).order_by(MenuItem.id)):
        out[item.name].append(item)
    for rows in out.values():
        rows.sort(key=lambda r: (_SIZE_SORT.get(r.size_code or SizeCode.ONE, 9), r.id))
    return out


def _product_out(
    row: ShopProduct,
    *,
    sizes: list[MenuItem],
    photos: dict[int, str],
    slug_by_ops_name: dict[str, str],
    explicit: list[int],
    by_category: dict[str, list[int]],
) -> ProductAdminOut:
    active = [s for s in sizes if s.active]
    photo_id = row.photo_asset_id
    own = photo_id is not None
    if photo_id is None:
        photo_id = next((s.photo_asset_id for s in sizes if s.photo_asset_id is not None), None)
    category_slug = (
        slug_by_ops_name.get(row.category_ops_name) if row.category_ops_name is not None else None
    )
    effective = list(explicit)
    if category_slug is not None:
        for gid in by_category.get(category_slug, ()):
            if gid not in effective:
                effective.append(gid)
    kcal_by_size = row.kcal_by_size or {}
    return ProductAdminOut(
        id=row.id,
        item_name=row.item_name,
        display_name=row.display_name,
        name=row.display_name or row.item_name,
        slug=product_slug(row),
        category_ops_name=row.category_ops_name,
        category_slug=category_slug,
        description=row.description,
        note=row.note,
        kcal=row.kcal,
        kcal_by_size={str(k): int(v) for k, v in kcal_by_size.items()}
        if row.kcal_by_size
        else None,
        nutrition={str(k): str(v) for k, v in row.nutrition.items()} if row.nutrition else None,
        allergens=tuple(row.allergens),
        allergens_state=cast(Any, allergens_state(row.allergens or [])),
        dietary=tuple(row.dietary),
        default_size=cast(Any, row.default_size),
        ingredients_text=row.ingredients_text,
        photo_url=photos.get(photo_id) if photo_id is not None else None,
        photo_is_own=own,
        badge=row.badge,
        sort_order=row.sort_order,
        visible=row.visible,
        available=row.available,
        featured=row.featured,
        ops_active=bool(active),
        from_price_pence=min((s.price_pence for s in active), default=None),
        sizes=tuple(
            ProductSizeOut(
                menu_item_id=s.id,
                code=cast(Any, (s.size_code or SizeCode.ONE).name),
                label=_SIZE_LABELS[s.size_code or SizeCode.ONE],
                price_pence=s.price_pence,
                active=s.active,
                kcal=(
                    int(kcal_by_size[(s.size_code or SizeCode.ONE).name])
                    if (s.size_code or SizeCode.ONE).name in kcal_by_size
                    else None
                ),
            )
            for s in sizes
        ),
        option_group_ids=tuple(explicit),
        effective_option_group_ids=tuple(effective),
        updated_at=row.updated_at,
    )


def _option_out(
    o: ShopOption, photos: dict[int, str], modifier_names: dict[int, str]
) -> OptionAdminOut:
    return OptionAdminOut(
        id=o.id,
        name=o.name,
        description=o.description,
        price_delta_pence=o.price_delta_pence,
        kcal=o.kcal,
        is_default=o.is_default,
        available=o.available,
        sort_order=o.sort_order,
        modifier_id=o.modifier_id,
        modifier_name=modifier_names.get(o.modifier_id) if o.modifier_id is not None else None,
        photo_url=photos.get(o.photo_asset_id) if o.photo_asset_id is not None else None,
    )


def _group_out(
    g: ShopOptionGroup,
    *,
    photos: dict[int, str],
    modifier_names: dict[int, str],
    product_ids: list[int],
) -> OptionGroupAdminOut:
    return OptionGroupAdminOut(
        id=g.id,
        name=g.name,
        prompt=g.prompt,
        kind=cast(Any, g.kind.name),
        layout=cast(Any, g.layout.name),
        required=g.required,
        min_select=g.min_select,
        max_select=g.max_select,
        collapsed=g.collapsed,
        applies_to_categories=tuple(g.applies_to_categories),
        sort_order=g.sort_order,
        active=g.active,
        options=tuple(_option_out(o, photos, modifier_names) for o in g.options),
        product_ids=tuple(product_ids),
        updated_at=g.updated_at,
    )


def _upsell_out(u: ShopUpsell) -> UpsellAdminOut:
    return UpsellAdminOut(
        id=u.id,
        placement=cast(Any, u.placement.name),
        heading=u.heading,
        product_ids=tuple(int(i) for i in u.product_ids),
        sort_order=u.sort_order,
        active=u.active,
    )


def _banner_out(b: ShopBanner, photos: dict[int, str]) -> BannerAdminOut:
    return BannerAdminOut(
        id=b.id,
        title=b.title,
        subtitle=b.subtitle,
        photo_url=photos.get(b.photo_asset_id) if b.photo_asset_id is not None else None,
        link_href=b.link_href,
        active=b.active,
        sort_order=b.sort_order,
        starts_on=b.starts_on,
        ends_on=b.ends_on,
    )


def _modifier_names(session: Session) -> dict[int, str]:
    return {m.id: m.name for m in session.scalars(select(Modifier))}


def _group_product_ids(session: Session) -> dict[int, list[int]]:
    out: dict[int, list[int]] = defaultdict(list)
    for product_id, group_id in session.execute(
        select(ShopProductOptionGroup.product_id, ShopProductOptionGroup.group_id).order_by(
            ShopProductOptionGroup.sort_order, ShopProductOptionGroup.product_id
        )
    ):
        out[group_id].append(product_id)
    return out


def _product_group_ids(session: Session) -> dict[int, list[int]]:
    out: dict[int, list[int]] = defaultdict(list)
    for product_id, group_id in session.execute(
        select(ShopProductOptionGroup.product_id, ShopProductOptionGroup.group_id).order_by(
            ShopProductOptionGroup.sort_order, ShopProductOptionGroup.group_id
        )
    ):
        out[product_id].append(group_id)
    return out


def _groups_by_category(session: Session) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for g in session.scalars(
        select(ShopOptionGroup).order_by(ShopOptionGroup.sort_order, ShopOptionGroup.id)
    ):
        for slug in g.applies_to_categories:
            out[str(slug)].append(g.id)
    return out


def catalogue_view(session: Session) -> CatalogueAdminOut:
    guarded(lambda: admin.sync_catalogue(session))
    categories = list(
        session.scalars(select(ShopCategory).order_by(ShopCategory.sort_order, ShopCategory.id))
    )
    products = list(
        session.scalars(
            select(ShopProduct).order_by(
                ShopProduct.category_ops_name, ShopProduct.sort_order, ShopProduct.item_name
            )
        )
    )
    groups = list(
        session.scalars(
            select(ShopOptionGroup)
            .options(selectinload(ShopOptionGroup.options))
            .order_by(ShopOptionGroup.sort_order, ShopOptionGroup.id)
        )
    )
    upsells = list(
        session.scalars(select(ShopUpsell).order_by(ShopUpsell.sort_order, ShopUpsell.id))
    )
    banners = list(
        session.scalars(select(ShopBanner).order_by(ShopBanner.sort_order, ShopBanner.id))
    )
    sizes = _sizes_by_name(session)
    photo_ids: list[int | None] = [c.photo_asset_id for c in categories]
    photo_ids += [p.photo_asset_id for p in products]
    photo_ids += [s.photo_asset_id for rows in sizes.values() for s in rows]
    photo_ids += [o.photo_asset_id for g in groups for o in g.options]
    photo_ids += [b.photo_asset_id for b in banners]
    photos = _photos(session, photo_ids)
    slug_by_ops_name = {c.ops_name: c.slug for c in categories}
    product_counts: dict[str, int] = defaultdict(int)
    for p in products:
        if p.category_ops_name is not None:
            product_counts[p.category_ops_name] += 1
    modifier_names = _modifier_names(session)
    group_products = _group_product_ids(session)
    product_groups = _product_group_ids(session)
    by_category = _groups_by_category(session)
    return CatalogueAdminOut(
        categories=tuple(_category_out(c, photos, product_counts) for c in categories),
        products=tuple(
            _product_out(
                p,
                sizes=sizes.get(p.item_name, []),
                photos=photos,
                slug_by_ops_name=slug_by_ops_name,
                explicit=product_groups.get(p.id, []),
                by_category=by_category,
            )
            for p in products
        ),
        option_groups=tuple(
            _group_out(
                g,
                photos=photos,
                modifier_names=modifier_names,
                product_ids=group_products.get(g.id, []),
            )
            for g in groups
        ),
        upsells=tuple(_upsell_out(u) for u in upsells),
        banners=tuple(_banner_out(b, photos) for b in banners),
        ops=OpsOut(
            items_without_category=sum(1 for p in products if p.category_ops_name is None),
            menu_items_active=admin.menu_items_active(session),
        ),
        allergens=tuple(ALLERGENS),
        dietary=tuple(DIETARY),
    )


# --- categories -------------------------------------------------------------


def _category_view(session: Session, row: ShopCategory) -> CategoryAdminOut:
    count = int(
        session.scalar(
            select(func.count())
            .select_from(ShopProduct)
            .where(ShopProduct.category_ops_name == row.ops_name)
        )
        or 0
    )
    return _category_out(row, _photos(session, [row.photo_asset_id]), {row.ops_name: count})


def category_update_view(session: Session, category_id: int, body: CategoryIn) -> CategoryAdminOut:
    changes = body.model_dump(exclude_unset=True)
    row = guarded(lambda: admin.category_update(session, category_id, changes))
    return _category_view(session, row)


def categories_order_view(session: Session, ids: list[int]) -> CatalogueAdminOut:
    guarded(lambda: admin.categories_order(session, ids))
    return catalogue_view(session)


def category_photo_view(
    session: Session, category_id: int, data: bytes | None, actor: str | None
) -> PhotoOut:
    guarded(lambda: admin.category_or_404(session, category_id))  # 404 before any file is written
    if data is None:
        guarded(lambda: admin.set_category_photo(session, category_id, None))
        return _no_photo()
    stored = guarded(lambda: store_image(session, data, uploaded_by=actor))
    guarded(lambda: admin.set_category_photo(session, category_id, stored.asset_id))
    return _photo(stored)


# --- products ---------------------------------------------------------------


def _product_view(session: Session, row: ShopProduct) -> ProductAdminOut:
    sizes = list(
        session.scalars(
            select(MenuItem).where(MenuItem.name == row.item_name).order_by(MenuItem.id)
        )
    )
    sizes.sort(key=lambda r: (_SIZE_SORT.get(r.size_code or SizeCode.ONE, 9), r.id))
    photos = _photos(session, [row.photo_asset_id, *(s.photo_asset_id for s in sizes)])
    slug_by_ops_name = {c.ops_name: c.slug for c in session.scalars(select(ShopCategory))}
    explicit = [
        gid
        for (gid,) in session.execute(
            select(ShopProductOptionGroup.group_id)
            .where(ShopProductOptionGroup.product_id == row.id)
            .order_by(ShopProductOptionGroup.sort_order, ShopProductOptionGroup.group_id)
        )
    ]
    return _product_out(
        row,
        sizes=sizes,
        photos=photos,
        slug_by_ops_name=slug_by_ops_name,
        explicit=explicit,
        by_category=_groups_by_category(session),
    )


def product_update_view(session: Session, product_id: int, body: ProductIn) -> ProductAdminOut:
    changes = body.model_dump(exclude_unset=True)
    row = guarded(lambda: admin.product_update(session, product_id, changes))
    return _product_view(session, row)


def _by_menu_item(session: Session, menu_item_id: int, row: ShopProduct) -> ProductByMenuItemOut:
    product = _product_view(session, row)
    ids = list(product.effective_option_group_ids)
    groups = {
        g.id: g for g in session.scalars(select(ShopOptionGroup).where(ShopOptionGroup.id.in_(ids)))
    }
    return ProductByMenuItemOut(
        **product.model_dump(),
        menu_item_id=menu_item_id,
        effective_option_groups=tuple(
            EffectiveGroupOut(id=g.id, name=g.name, kind=cast(Any, g.kind.name))
            for g in (groups[i] for i in ids if i in groups)
        ),
        shop_url_path=f"/order/p/{product.slug}",
    )


def product_by_menu_item_view(session: Session, menu_item_id: int) -> ProductByMenuItemOut:
    row = guarded(lambda: admin.product_for_menu_item(session, menu_item_id))
    return _by_menu_item(session, menu_item_id, row)


def product_update_by_menu_item_view(
    session: Session, menu_item_id: int, body: ProductIn
) -> ProductByMenuItemOut:
    row = guarded(lambda: admin.product_for_menu_item(session, menu_item_id))
    changes = body.model_dump(exclude_unset=True)
    row = guarded(lambda: admin.product_update(session, row.id, changes))
    return _by_menu_item(session, menu_item_id, row)


def products_order_view(session: Session, category_slug: str, ids: list[int]) -> CatalogueAdminOut:
    guarded(lambda: admin.products_order(session, category_slug, ids))
    return catalogue_view(session)


def products_bulk_view(session: Session, body: ProductsBulkIn) -> BulkOut:
    rows = guarded(
        lambda: admin.products_bulk(
            session, body.ids, available=body.available, visible=body.visible
        )
    )
    return BulkOut(changed=len(rows), products=tuple(_product_view(session, r) for r in rows))


def product_photo_view(
    session: Session, product_id: int, data: bytes | None, actor: str | None
) -> PhotoOut:
    guarded(lambda: admin.product_or_404(session, product_id))
    if data is None:
        guarded(lambda: admin.set_product_photo(session, product_id, None))
        return _no_photo()
    stored = guarded(lambda: store_image(session, data, uploaded_by=actor))
    guarded(lambda: admin.set_product_photo(session, product_id, stored.asset_id))
    return _photo(stored)


# --- option groups ------------------------------------------------------------


def _group_view(session: Session, row: ShopOptionGroup) -> OptionGroupAdminOut:
    session.refresh(row, attribute_names=["options"])
    photos = _photos(session, [o.photo_asset_id for o in row.options])
    product_ids = [
        pid
        for (pid,) in session.execute(
            select(ShopProductOptionGroup.product_id)
            .where(ShopProductOptionGroup.group_id == row.id)
            .order_by(ShopProductOptionGroup.sort_order, ShopProductOptionGroup.product_id)
        )
    ]
    return _group_out(
        row, photos=photos, modifier_names=_modifier_names(session), product_ids=product_ids
    )


def option_group_create_view(session: Session, body: OptionGroupIn) -> OptionGroupAdminOut:
    row = guarded(lambda: admin.option_group_create(session, body.model_dump()))
    return _group_view(session, row)


def option_group_update_view(
    session: Session, group_id: int, body: OptionGroupIn
) -> OptionGroupAdminOut:
    row = guarded(lambda: admin.option_group_update(session, group_id, body.model_dump()))
    return _group_view(session, row)


def option_group_delete_view(session: Session, group_id: int) -> DeletedOut:
    guarded(lambda: admin.delete_option_group(session, group_id))
    return DeletedOut(id=group_id, deleted=True)


def option_groups_order_view(session: Session, ids: list[int]) -> CatalogueAdminOut:
    guarded(lambda: admin.option_groups_order(session, ids))
    return catalogue_view(session)


def option_photo_view(
    session: Session, option_id: int, data: bytes | None, actor: str | None
) -> PhotoOut:
    if session.get(ShopOption, option_id) is None:
        raise HTTPException(status_code=404, detail=f"option {option_id} does not exist")
    if data is None:
        guarded(lambda: admin.set_option_photo(session, option_id, None))
        return _no_photo()
    stored = guarded(lambda: store_image(session, data, uploaded_by=actor))
    guarded(lambda: admin.set_option_photo(session, option_id, stored.asset_id))
    return _photo(stored)


# --- upsells ------------------------------------------------------------------


def upsell_create_view(session: Session, body: UpsellIn) -> UpsellAdminOut:
    return _upsell_out(guarded(lambda: admin.upsell_create(session, body.model_dump())))


def upsell_update_view(session: Session, upsell_id: int, body: UpsellIn) -> UpsellAdminOut:
    return _upsell_out(guarded(lambda: admin.upsell_update(session, upsell_id, body.model_dump())))


def upsell_delete_view(session: Session, upsell_id: int) -> DeletedOut:
    guarded(lambda: admin.upsell_delete(session, upsell_id))
    return DeletedOut(id=upsell_id, deleted=True)


# --- banners ------------------------------------------------------------------


def _banner_view(session: Session, row: ShopBanner) -> BannerAdminOut:
    return _banner_out(row, _photos(session, [row.photo_asset_id]))


def banner_create_view(session: Session, body: BannerIn) -> BannerAdminOut:
    return _banner_view(session, guarded(lambda: admin.banner_create(session, body.model_dump())))


def banner_update_view(session: Session, banner_id: int, body: BannerIn) -> BannerAdminOut:
    return _banner_view(
        session, guarded(lambda: admin.banner_update(session, banner_id, body.model_dump()))
    )


def banner_delete_view(session: Session, banner_id: int) -> DeletedOut:
    guarded(lambda: admin.banner_delete(session, banner_id))
    return DeletedOut(id=banner_id, deleted=True)


def banners_order_view(session: Session, ids: list[int]) -> CatalogueAdminOut:
    guarded(lambda: admin.banners_order(session, ids))
    return catalogue_view(session)


def banner_photo_view(
    session: Session, banner_id: int, data: bytes | None, actor: str | None
) -> PhotoOut:
    guarded(lambda: admin.banner_or_404(session, banner_id))
    if data is None:
        guarded(lambda: admin.set_banner_photo(session, banner_id, None))
        return _no_photo()
    stored = guarded(lambda: store_image(session, data, uploaded_by=actor))
    guarded(lambda: admin.set_banner_photo(session, banner_id, stored.asset_id))
    return _photo(stored)


# --- modifiers ----------------------------------------------------------------


def modifiers_view(session: Session) -> tuple[ModifierOut, ...]:
    return tuple(
        ModifierOut(id=m.id, name=m.name, price_pence=m.price_pence)
        for m in admin.modifiers(session)
    )


# --- photo helpers --------------------------------------------------------------


def _photo(stored: Any) -> PhotoOut:
    return PhotoOut(
        asset_id=stored.asset_id,
        photo_url=stored.url,
        width=stored.width,
        height=stored.height,
        bytes=stored.bytes,
        content_type=stored.content_type,
    )


def _no_photo() -> PhotoOut:
    return PhotoOut(
        asset_id=None, photo_url=None, width=None, height=None, bytes=None, content_type=None
    )


# ==========================================================================
# Insights
# ==========================================================================


def insights_view(session: Session, *, since: Any, until: Any, days: int) -> ShopInsightsOut:
    r = insights.shop_insights(session, since=since, until=until, days=days)
    f = r.figures
    return ShopInsightsOut(
        period=InsightsPeriodOut.model_validate({"from": r.since, "to": r.until, "days": r.days}),
        previous=InsightsPreviousOut.model_validate({"from": r.prev_since, "to": r.prev_until}),
        figures=InsightsFiguresOut(
            orders=f.orders,
            revenue_pence=f.revenue_pence,
            avg_basket_pence=f.avg_basket_pence,
            items=f.items,
            members_share=f.members_share,
            online_paid_share=f.online_paid_share,
            cancelled=f.cancelled,
            rejected=f.rejected,
            avg_minutes_to_ready=f.avg_minutes_to_ready,
            vs_previous=InsightsVsPreviousOut(
                orders_pct=f.vs_previous.orders_pct,
                revenue_pct=f.vs_previous.revenue_pct,
                avg_basket_pct=f.vs_previous.avg_basket_pct,
            ),
        ),
        per_day=tuple(
            InsightsDayOut(
                date=d.date, orders=d.orders, revenue_pence=d.revenue_pence, cancelled=d.cancelled
            )
            for d in r.per_day
        ),
        by_hour=tuple(InsightsHourOut(hour=h.hour, orders=h.orders) for h in r.by_hour),
        by_weekday=tuple(
            InsightsWeekdayOut(weekday=w.weekday, orders=w.orders, revenue_pence=w.revenue_pence)
            for w in r.by_weekday
        ),
        top_products=tuple(
            InsightsProductOut(
                product_id=p.product_id, name=p.name, qty=p.qty, revenue_pence=p.revenue_pence
            )
            for p in r.top_products
        ),
        top_options=tuple(
            InsightsOptionOut(group=o.group, name=o.name, qty=o.qty) for o in r.top_options
        ),
        dining=InsightsDiningOut(takeaway=r.dining_takeaway, eat_in=r.dining_eat_in),
        payment=InsightsPaymentOut(counter=r.payment_counter, online=r.payment_online),
        status_now=InsightsStatusNowOut(
            new=r.status_new,
            accepted=r.status_accepted,
            preparing=r.status_preparing,
            ready=r.status_ready,
        ),
        caveats=tuple(r.caveats),
    )
