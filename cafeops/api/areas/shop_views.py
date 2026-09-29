"""What each public shop route returns, built inside the session (docs/shop/CONTRACT.md §4).

Every function takes a `Session`, reads through `services/shop`, and returns a finished
Pydantic model: nothing lazy survives the session closing (`api/runtime.py`).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.areas.shop_schemas import (
    AsapOut,
    BannerOut,
    CafeOut,
    CatalogueOut,
    CategoryOut,
    ConfigOut,
    DiningOut,
    LoyaltyBlurbOut,
    MeOut,
    MePaymentOut,
    MeRewardOut,
    OptionGroupOut,
    OptionOut,
    OrderCustomerOut,
    OrderEventOut,
    OrderLineOut,
    OrderNotifyOut,
    OrderOut,
    PaymentOut,
    PayOut,
    PlacedOut,
    PlaceOrderIn,
    ProductOut,
    PushConfigOut,
    PushSubscribedOut,
    PushSubscribeIn,
    QuotedLineOut,
    QuotedOptionOut,
    QuoteIn,
    QuoteOut,
    RecentLineOut,
    RecentOrderOut,
    RewardOut,
    SavedMethodOut,
    SizeOut,
    SlotOut,
    SlotsOut,
    UpsellOut,
    WebhookOut,
)
from cafeops.config import settings
from cafeops.db.models import (
    DiningOption,
    LoyaltyCard,
    OrderStatus,
    ShopOrder,
    ShopPaymentMethod,
    ShopPushSubscription,
)
from cafeops.domain.shop import (
    CUSTOMER_CANCELLABLE,
    STATUS_LABELS,
    display_code,
    status_step,
)
from cafeops.services.loyalty.common import authenticate_card, default_program
from cafeops.services.shop import catalog, notify, orders, payments, pricing, slots
from cafeops.services.shop.errors import ShopError
from cafeops.services.shop.pricing import available_reward_for

__all__ = [
    "cancel_view",
    "catalogue_view",
    "config_view",
    "me_view",
    "order_view",
    "place_order_view",
    "push_subscribe_view",
    "push_unsubscribe_view",
    "quote_view",
    "slots_view",
    "webhook_view",
]

#: Constants from site/BRIEF.md.
CAFE = CafeOut(
    name="Sasha's Corner",
    address_line="23 Commercial Street, Dundee",
    postcode="DD1 3DD",
    phone="07398 433317",
)

_STATUS_KEYS: set[str] = set(STATUS_LABELS)


def _dining_key(value: DiningOption) -> Literal["takeaway", "eat_in"]:
    return "takeaway" if value is DiningOption.TAKEAWAY else "eat_in"


def _card(session: Session, token: str | None) -> LoyaltyCard | None:
    """The card a token names, or None (a bad token is just "not signed in" here)."""
    if not token:
        return None
    card = session.scalar(select(LoyaltyCard).where(LoyaltyCard.auth_token == token))
    if card is None or card.voided_at is not None:
        return None
    return card


def _require_card(session: Session, card_id: str | None, token: str | None) -> LoyaltyCard:
    if card_id:
        return authenticate_card(session, card_id, token)
    card = _card(session, token)
    if card is None:
        raise ShopError(401, "bad_token", "Sign in to your Rewards card first.")
    return card


# --------------------------------------------------------------------------
# config and catalogue
# --------------------------------------------------------------------------


def config_view(session: Session) -> ConfigOut:
    now = datetime.now(UTC)
    shop = catalog.load_settings(session)
    program = default_program(session)
    online = payments.online_payment_offered(shop)
    return ConfigOut(
        enabled=shop.enabled,
        closed_message=shop.closed_message,
        hero_title=shop.hero_title,
        hero_subtitle=shop.hero_subtitle,
        dining=DiningOut(
            takeaway=shop.takeaway_enabled,
            eat_in=shop.eat_in_enabled,
            default=_dining_key(shop.default_dining),
        ),
        pay=PayOut(
            counter=shop.pay_at_counter,
            online=online,
            provider=shop.payment_provider if online else None,
        ),
        kcal_notice=shop.kcal_notice,
        allergen_notice=shop.allergen_notice,
        collection_note=shop.collection_note,
        terms_url=shop.terms_url,
        open_now=slots.open_now(shop, now),
        next_open_local=slots.next_open_local(shop, now),
        cafe=CAFE,
        loyalty=LoyaltyBlurbOut(
            program_name=program.name,
            stamps_required=program.stamps_required,
            reward_text=program.reward_text,
        ),
        push=PushConfigOut(
            enabled=bool(shop.push_notify and settings.push_configured),
            vapid_public_key=settings.vapid_public_key if shop.push_notify else None,
        ),
    )


def catalogue_view(session: Session) -> CatalogueOut:
    cat = catalog.catalogue(session)
    return CatalogueOut(
        generated_at=cat.generated_at,
        version=cat.version,
        banners=[BannerOut(**asdict(b)) for b in cat.banners],
        categories=[CategoryOut(**asdict(c)) for c in cat.categories],
        products=[
            ProductOut(
                id=p.id,
                slug=p.slug,
                name=p.name,
                category_slug=p.category_slug,
                description=p.description,
                note=p.note,
                kcal=p.kcal,
                kcal_by_size=p.kcal_by_size,
                nutrition=p.nutrition,
                allergens=list(p.allergens),
                allergens_state=p.allergens_state,  # type: ignore[arg-type]
                dietary=list(p.dietary),
                ingredients_text=p.ingredients_text,
                photo_url=p.photo_url,
                badge=p.badge,
                available=p.available,
                featured=p.featured,
                from_price_pence=p.from_price_pence,
                default_size=p.default_size,  # type: ignore[arg-type]
                sizes=[SizeOut(**asdict(s)) for s in p.sizes],
                option_groups=[
                    OptionGroupOut(
                        id=g.id,
                        name=g.name,
                        prompt=g.prompt,
                        kind=g.kind,  # type: ignore[arg-type]
                        layout=g.layout,  # type: ignore[arg-type]
                        required=g.required,
                        min_select=g.min_select,
                        max_select=g.max_select,
                        collapsed=g.collapsed,
                        options=[OptionOut(**asdict(o)) for o in g.options],
                    )
                    for g in p.option_groups
                ],
                upsells=[
                    UpsellOut(heading=u.heading, product_ids=list(u.product_ids)) for u in p.upsells
                ],
            )
            for p in cat.products
        ],
        basket_upsells=[
            UpsellOut(heading=u.heading, product_ids=list(u.product_ids))
            for u in cat.basket_upsells
        ],
    )


def slots_view(session: Session, day: date | None) -> SlotsOut:
    shop = catalog.load_settings(session)
    view = slots.slots_for(session, shop, day)
    return SlotsOut(
        date=view.day,
        open=view.open,
        reason=view.reason,
        asap=AsapOut(available=view.asap.available, at=view.asap.at, local=view.asap.local),
        slots=[SlotOut(at=s.at, local=s.local, available=s.available) for s in view.slots],
        days=list(view.days),
    )


# --------------------------------------------------------------------------
# quote, place, status, cancel
# --------------------------------------------------------------------------


def _lines_in(body: QuoteIn | PlaceOrderIn) -> tuple[pricing.LineIn, ...]:
    return tuple(
        pricing.LineIn(
            product_id=ln.product_id,
            menu_item_id=ln.menu_item_id,
            qty=ln.qty,
            option_ids=tuple(ln.option_ids),
        )
        for ln in body.lines
    )


def _quote_out(q: pricing.Quote) -> QuoteOut:
    return QuoteOut(
        lines=[
            QuotedLineOut(
                product_id=ln.product_id,
                menu_item_id=ln.menu_item_id,
                qty=ln.qty,
                option_ids=list(ln.option_ids),
                name=ln.name,
                size_label=ln.size_label,
                unit_price_pence=ln.unit_price_pence,
                line_total_pence=ln.line_total_pence,
                options=[
                    QuotedOptionOut(
                        group=o.group, name=o.name, price_delta_pence=o.price_delta_pence
                    )
                    for o in ln.options
                ],
                problems=list(ln.problems),
            )
            for ln in q.lines
        ],
        subtotal_pence=q.subtotal_pence,
        discount_pence=q.discount_pence,
        total_pence=q.total_pence,
        reward=RewardOut(
            applied=q.reward.applied, line_index=q.reward.line_index, text=q.reward.text
        ),
        problems=list(q.problems),
    )


def quote_view(session: Session, body: QuoteIn, token: str | None) -> QuoteOut:
    card = _card(session, token)
    return _quote_out(pricing.quote(session, lines=_lines_in(body), reward=body.reward, card=card))


def _payment_out(order: ShopOrder, checkout_url: str | None = None) -> PaymentOut:
    return PaymentOut(
        method="online" if order.payment_method is ShopPaymentMethod.ONLINE else "counter",
        status=order.payment_status.value.lower(),  # type: ignore[arg-type]
        checkout_url=checkout_url,
    )


def place_order_view(
    session: Session,
    body: PlaceOrderIn,
    token: str | None,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> tuple[PlacedOut, int]:
    """Returns the response and the order id (for the background notifications)."""
    card = _card(session, token)
    req = orders.PlaceRequest(
        dining=body.dining,
        asap=body.asap,
        requested_at=body.requested_at,
        lines=_lines_in(body),
        reward=body.reward,
        customer_name=body.customer.name,
        customer_phone=body.customer.phone,
        customer_email=body.customer.email,
        note=body.note,
        table=body.table,
        allergy_ack=body.allergy_ack,
        payment=body.payment,
        expected_total_pence=body.expected_total_pence,
        sms_opt_in=body.sms_opt_in,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    order, _priced = orders.place_order(session, req, card=card)
    checkout_url: str | None = None
    if order.payment_method is ShopPaymentMethod.ONLINE:
        checkout_url = payments.start_checkout(session, order, card=card)
    return (
        PlacedOut(
            code=display_code(order.code),
            status=order.status.value,
            access_token=order.access_token,
            total_pence=order.total_pence,
            requested_at=order.requested_at,
            requested_local=slots.local_label(order.requested_at),
            payment=_payment_out(order, checkout_url),
        ),
        order.id,
    )


def _order_out(session: Session, order: ShopOrder) -> OrderOut:
    shop = catalog.load_settings(session)
    return OrderOut(
        code=display_code(order.code),
        status=order.status.value,
        status_label=STATUS_LABELS.get(order.status.value, order.status.value),
        status_step=status_step(order.status.value),
        dining=_dining_key(order.dining),
        table=order.table,
        asap=order.asap,
        requested_at=order.requested_at,
        requested_local=slots.local_label(order.requested_at),
        placed_at=order.placed_at,
        ready_at=order.ready_at,
        collected_at=order.collected_at,
        customer=OrderCustomerOut(name=order.customer_name),
        lines=[
            OrderLineOut(
                name=ln.name,
                size_label=ln.size_label,
                qty=ln.qty,
                unit_price_pence=ln.unit_price_pence,
                line_total_pence=ln.line_total_pence,
                options=[
                    QuotedOptionOut(
                        group=str(o.get("group", "")),
                        name=str(o.get("name", "")),
                        price_delta_pence=int(o.get("price_delta_pence", 0) or 0),
                    )
                    for o in ln.options
                    if isinstance(o, dict)
                ],
            )
            for ln in order.lines
        ],
        subtotal_pence=order.subtotal_pence,
        discount_pence=order.discount_pence,
        total_pence=order.total_pence,
        payment=_payment_out(order),
        collection_note=shop.collection_note,
        cancel_allowed=order.status.value in CUSTOMER_CANCELLABLE,
        notify=OrderNotifyOut(**notify.notify_state(session, shop, order)),
        events=[
            OrderEventOut(at=e.at, kind=e.kind, detail=e.detail)
            for e in order.events
            if e.actor != "system" or e.kind in ("refund_needed", "reward_unavailable")
        ],
    )


def order_view(session: Session, code: str, token: str | None) -> OrderOut:
    return _order_out(session, orders.load_order_by_code(session, code, token))


def cancel_view(session: Session, code: str, token: str | None) -> OrderOut:
    order = orders.load_order_by_code(session, code, token)
    orders.customer_cancel(session, order)
    return _order_out(session, order)


# --------------------------------------------------------------------------
# push subscriptions (§3c)
# --------------------------------------------------------------------------


def _push_state(session: Session, order: ShopOrder) -> PushSubscribedOut:
    live = session.scalars(
        select(ShopPushSubscription.id).where(
            ShopPushSubscription.order_id == order.id, ShopPushSubscription.expired_at.is_(None)
        )
    )
    n = len(list(live))
    return PushSubscribedOut(subscribed=n > 0, devices=n)


def push_subscribe_view(
    session: Session, code: str, token: str | None, body: PushSubscribeIn
) -> PushSubscribedOut:
    order = orders.load_order_by_code(session, code, token)
    existing = session.scalar(
        select(ShopPushSubscription).where(
            ShopPushSubscription.order_id == order.id,
            ShopPushSubscription.endpoint == body.endpoint,
        )
    )
    now = datetime.now(UTC)
    if existing is None:
        session.add(
            ShopPushSubscription(
                order_id=order.id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
                created_at=now,
            )
        )
    else:
        existing.p256dh, existing.auth, existing.expired_at = body.keys.p256dh, body.keys.auth, None
    session.flush()
    return _push_state(session, order)


def push_unsubscribe_view(
    session: Session, code: str, token: str | None, endpoint: str | None
) -> PushSubscribedOut:
    order = orders.load_order_by_code(session, code, token)
    now = datetime.now(UTC)
    for sub in session.scalars(
        select(ShopPushSubscription).where(
            ShopPushSubscription.order_id == order.id, ShopPushSubscription.expired_at.is_(None)
        )
    ):
        if endpoint is None or sub.endpoint == endpoint:
            sub.expired_at = now
    session.flush()
    return _push_state(session, order)


# --------------------------------------------------------------------------
# me
# --------------------------------------------------------------------------


def _reorder_lines(order: ShopOrder, cat: catalog.Catalogue) -> tuple[list[RecentLineOut], bool]:
    """The order's lines as basket lines, keeping only what the catalogue still has.
    Option ids are stored on the line since 2026-09-29; an older line's options are
    matched by group and option name, and a line with an unmatched option is dropped."""
    by_product = {p.id: p for p in cat.products}
    out: list[RecentLineOut] = []
    complete = True
    for ln in order.lines:
        product = by_product.get(ln.product_id) if ln.product_id is not None else None
        if product is None or ln.menu_item_id not in {s.menu_item_id for s in product.sizes}:
            complete = False
            continue
        by_id = {o.id for g in product.option_groups for o in g.options}
        by_name = {(g.name, o.name): o.id for g in product.option_groups for o in g.options}
        option_ids: list[int] = []
        for opt in ln.options:
            if not isinstance(opt, dict):
                continue
            raw = opt.get("option_id")
            oid = (
                int(raw)
                if raw is not None
                else by_name.get((str(opt.get("group", "")), str(opt.get("name", ""))))
            )
            if oid is None or oid not in by_id:
                option_ids = []
                break
            option_ids.append(oid)
        else:
            out.append(
                RecentLineOut(
                    product_id=product.id,
                    menu_item_id=ln.menu_item_id,
                    qty=ln.qty,
                    option_ids=option_ids,
                )
            )
            continue
        complete = False
    return out, complete


def _recent_out(order: ShopOrder, cat: catalog.Catalogue) -> RecentOrderOut:
    lines, complete = _reorder_lines(order, cat)
    return RecentOrderOut(
        code=display_code(order.code),
        status=order.status.value,
        placed_at=order.placed_at,
        total_pence=order.total_pence,
        lines=lines,
        reorder_complete=complete,
    )


def me_view(session: Session, token: str | None, card_id: str | None = None) -> MeOut:
    card = _require_card(session, card_id, token)
    now = datetime.now(UTC)
    member = card.member
    reward = available_reward_for(session, card, now)
    recent = session.scalars(
        select(ShopOrder)
        .where(ShopOrder.member_id == member.id)
        .order_by(ShopOrder.placed_at.desc())
        .limit(10)
    )
    summary = payments.payment_summary(session, card)
    cat = catalog.catalogue(session, now=now)
    return MeOut(
        first_name=member.first_name,
        email=member.email,
        phone=member.phone,
        card_id=card.id,
        stamps_current=card.stamps_current,
        stamps_required=card.program.stamps_required,
        reward=MeRewardOut(id=reward.id, text=card.program.reward_text) if reward else None,
        recent_orders=[_recent_out(o, cat) for o in recent],
        payment=(
            MePaymentOut(
                provider=summary.provider,
                saved_methods=[SavedMethodOut(**asdict(m)) for m in summary.saved_methods],
            )
            if summary
            else None
        ),
    )


# --------------------------------------------------------------------------
# webhook
# --------------------------------------------------------------------------


def webhook_view(
    session: Session, provider_key: str, headers: dict[str, str], body: bytes
) -> tuple[WebhookOut, int | None, bool]:
    """Returns (response, order id, became NEW) so the route can push to the POS."""
    outcome = payments.handle_webhook(session, provider_key, headers, body)
    became_new = False
    if outcome.kind == "paid" and outcome.order_id is not None:
        order = session.get(ShopOrder, outcome.order_id)
        became_new = order is not None and order.status is OrderStatus.NEW
    return (
        WebhookOut(received=True, kind=outcome.kind, detail=outcome.detail),
        outcome.order_id,
        became_new,
    )
