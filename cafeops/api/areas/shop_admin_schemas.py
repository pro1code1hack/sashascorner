"""Request and response models for the back office's Online orders area.

`docs/shop/CONTRACT.md` §5 is the shape; §2 is the vocabulary. Same conventions as
`api/schemas.py`: integer pence, UTC timestamps with offset, `*_local` display strings
in Europe/London, bodies forbid unknown fields. Enum-valued fields carry the database
member names (`NEW`, `TAKEAWAY`, `SINGLE`, `PHOTO_TILES` …) so a screen can send back
exactly what it was given.

Refusals are sentences in `{"detail": str}` (FRONTEND-KIT §1 rule 10): 404 for a
missing thing, 409 when the world moved (a bad status transition, a cancel after
collection), 422 for a request that cannot be honoured as asked.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from cafeops.api.schemas import In, Out

OrderStatusStr = Literal[
    "PENDING_PAYMENT",
    "NEW",
    "ACCEPTED",
    "PREPARING",
    "READY",
    "COLLECTED",
    "CANCELLED",
    "REJECTED",
]
DiningStr = Literal["TAKEAWAY", "EAT_IN"]
SmsNotifyStr = Literal["off", "ready", "all"]
PaymentMethodStr = Literal["COUNTER", "ONLINE"]
PaymentStatusStr = Literal["UNPAID", "PAID", "REFUNDED", "FAILED"]


def _upper(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


# The DB member names. The public catalogue spells them lowercase, so a curl written
# from the contract sends "single" / "tiles": `_upper` accepts either before validation.
OptionKindStr = Annotated[Literal["SINGLE", "MULTI"], BeforeValidator(_upper)]
OptionLayoutStr = Annotated[Literal["TILES", "PHOTO_TILES", "CHECKLIST"], BeforeValidator(_upper)]
UpsellPlacementStr = Annotated[Literal["ITEM_PAGE", "BASKET"], BeforeValidator(_upper)]
SizeCodeStr = Literal["S", "M", "XL", "ONE"]
HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM, 24h")]
BY = Field(min_length=1, max_length=80, description="Operator name. Required on every write.")


# ==========================================================================
# Summary
# ==========================================================================


class ProviderOut(Out):
    """A payment provider or a POS sink, as `registry.available()` describes it."""

    key: str
    display_name: str
    configured: bool


class SummaryCountsOut(Out):
    new: int
    accepted: int
    preparing: int
    ready: int
    today_collected: int
    today_cancelled: int


class NextDueOut(Out):
    id: int
    code: str
    requested_local: str
    customer_name: str
    status: OrderStatusStr


class SummaryOut(Out):
    enabled: bool
    open_now: bool
    counts: SummaryCountsOut
    today_revenue_pence: int = Field(description="Collected today, after any reward discount.")
    next_due: tuple[NextDueOut, ...]
    stripe_configured: bool
    telegram_configured: bool
    smtp_configured: bool = False
    sms_configured: bool = False
    push_configured: bool = False
    payment_providers: tuple[ProviderOut, ...] = Field(
        default=(), description="`integrations/payments/registry.available()`."
    )
    pos_sinks: tuple[ProviderOut, ...] = Field(
        default=(), description="Where a collected order can be pushed: none | lightspeed."
    )


# ==========================================================================
# Orders
# ==========================================================================


class LineOptionOut(Out):
    group: str
    name: str
    price_delta_pence: int
    modifier_id: int | None = None


class OrderLineOut(Out):
    id: int
    product_id: int | None
    menu_item_id: int
    name: str
    size_label: str
    qty: int
    unit_price_pence: int
    options: tuple[LineOptionOut, ...]
    line_total_pence: int
    sort_order: int


class OrderEventOut(Out):
    id: int
    at: datetime
    kind: str
    detail: str | None
    actor: str


class OrderMemberOut(Out):
    id: int
    first_name: str
    stamps_current: int


class OrderAdminOut(Out):
    """Every `shop_order` column plus lines, events and the member link (CONTRACT §5)."""

    id: int
    code: str
    code_display: str = Field(description='"SC-XXXXXX", as the customer sees it.')
    status: OrderStatusStr
    dining: DiningStr
    asap: bool
    requested_at: datetime
    requested_local: str
    placed_at: datetime
    placed_local: str
    minutes_until_due: int = Field(description="Negative when the slot has passed.")
    accepted_at: datetime | None
    ready_at: datetime | None
    collected_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    cancelled_by: str | None
    customer_name: str
    customer_phone: str | None
    customer_email: str | None
    member_id: int | None
    card_id: str | None
    member: OrderMemberOut | None
    note: str | None
    table: str | None = Field(default=None, description="Eat in: the customer's table.")
    staff_note: str | None
    allergy_ack: bool
    subtotal_pence: int
    discount_pence: int
    total_pence: int
    reward_id: int | None
    payment_method: PaymentMethodStr
    payment_status: PaymentStatusStr
    payment_ref: str | None
    payment_intent: str | None = Field(
        default=None, description="The provider's payment id (Stripe payment_intent)."
    )
    refundable: bool = Field(
        default=False, description="PAID online: `POST …/refund` would try the provider."
    )
    paid_at: datetime | None
    sale_receipt_id: str | None
    stamp_event_id: int | None
    pos_ref: str | None = Field(
        default=None, description="The POS sink's reference once the order was pushed."
    )
    client_ip_hash: str | None
    user_agent: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    lines: tuple[OrderLineOut, ...]
    events: tuple[OrderEventOut, ...]
    allowed_transitions: tuple[OrderStatusStr, ...] = Field(
        description="What `POST …/status` would accept from here (CONTRACT §3.8)."
    )


class OrdersPageOut(Out):
    items: tuple[OrderAdminOut, ...]
    total: int
    page: int
    page_size: int


class StatusIn(In):
    status: OrderStatusStr
    reason: str | None = Field(default=None, max_length=300)
    by: str = BY


class NoteIn(In):
    staff_note: str | None = Field(default=None, max_length=300)
    by: str = BY


class ByIn(In):
    by: str = BY


class RefundIn(In):
    by: str = BY
    reason: str | None = Field(default=None, max_length=300)


# ==========================================================================
# Settings
# ==========================================================================


class HoursRowOut(Out):
    weekday: int = Field(ge=0, le=6, description="Mon=0 … Sun=6")
    open: str
    close: str


class HoursRowIn(In):
    weekday: int = Field(ge=0, le=6)
    open: HHMM
    close: HHMM


class ClosureOut(Out):
    date: date
    note: str | None


class ClosureIn(In):
    date: date
    note: str | None = Field(default=None, max_length=120)


class ShopSettingsOut(Out):
    enabled: bool
    closed_message: str
    hero_title: str
    hero_subtitle: str
    takeaway_enabled: bool
    eat_in_enabled: bool
    default_dining: DiningStr
    lead_minutes: int
    slot_minutes: int
    max_orders_per_slot: int
    days_ahead: int
    hours: tuple[HoursRowOut, ...]
    last_order_minutes_before_close: int
    closures: tuple[ClosureOut, ...]
    pay_at_counter: bool
    pay_online: bool
    kcal_notice: str
    allergen_notice: str
    collection_note: str
    terms_url: str
    loyalty_stamps_online: bool
    notify_telegram: bool
    #: CONTRACT §10.J: off (default) = customers sign in with their Rewards card to place
    #: an order; the public API then answers 401 `sign_in_required` without a card token.
    guest_orders: bool = False
    payment_provider: str = Field(default="stripe", description="A key from `payment_providers`.")
    pos_sink: str = Field(default="none", description="none | lightspeed")
    #: CONTRACT §3c customer updates. Email and push are free; SMS costs a few pence a
    #: text, so "ready" (one per order) is the default.
    email_notify: bool = True
    push_notify: bool = True
    sms_notify: SmsNotifyStr = "ready"
    updated_at: datetime | None
    stripe_configured: bool = Field(
        description="`pay_online` is only honoured by the public API when this is true."
    )
    telegram_configured: bool
    smtp_configured: bool = False
    sms_configured: bool = False
    push_configured: bool = False


class ShopSettingsIn(In):
    """Any subset. Hours and closures are replaced wholesale when present."""

    enabled: bool | None = None
    closed_message: str | None = Field(default=None, max_length=300)
    hero_title: str | None = Field(default=None, max_length=120)
    hero_subtitle: str | None = Field(default=None, max_length=300)
    takeaway_enabled: bool | None = None
    eat_in_enabled: bool | None = None
    default_dining: DiningStr | None = None
    lead_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    slot_minutes: int | None = Field(default=None, ge=5, le=120)
    max_orders_per_slot: int | None = Field(default=None, ge=0, le=999)
    days_ahead: int | None = Field(default=None, ge=0, le=60)
    hours: list[HoursRowIn] | None = None
    last_order_minutes_before_close: int | None = Field(default=None, ge=0, le=24 * 60)
    closures: list[ClosureIn] | None = None
    pay_at_counter: bool | None = None
    pay_online: bool | None = None
    kcal_notice: str | None = Field(default=None, max_length=200)
    allergen_notice: str | None = Field(default=None, max_length=4000)
    collection_note: str | None = Field(default=None, max_length=300)
    terms_url: str | None = Field(default=None, max_length=300)
    loyalty_stamps_online: bool | None = None
    notify_telegram: bool | None = None
    guest_orders: bool | None = None
    payment_provider: str | None = Field(default=None, max_length=20)
    pos_sink: str | None = Field(default=None, max_length=20)
    email_notify: bool | None = None
    push_notify: bool | None = None
    sms_notify: SmsNotifyStr | None = None


# ==========================================================================
# Catalogue
# ==========================================================================


class PhotoOut(Out):
    asset_id: int | None
    photo_url: str | None
    width: int | None
    height: int | None
    bytes: int | None
    content_type: str | None


class CategoryAdminOut(Out):
    id: int
    ops_name: str
    name: str
    slug: str
    blurb: str | None
    photo_url: str | None
    sort_order: int
    visible: bool
    product_count: int = Field(description="Shop products in this category, visible or not.")
    updated_at: datetime | None


class CategoryIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    blurb: str | None = Field(default=None, max_length=300)
    visible: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


class OrderIdsIn(In):
    ids: list[int] = Field(min_length=1)


class ProductSizeOut(Out):
    menu_item_id: int
    code: SizeCodeStr
    label: str
    price_pence: int
    active: bool
    kcal: int | None


class ProductAdminOut(Out):
    id: int
    item_name: str
    display_name: str | None
    name: str = Field(description="`display_name` or `item_name`: what the shop shows.")
    slug: str
    category_ops_name: str | None
    category_slug: str | None
    description: str | None
    note: str | None
    kcal: int | None
    kcal_by_size: dict[str, int] | None
    nutrition: dict[str, str] | None
    allergens: tuple[str, ...]
    allergens_state: Literal["unknown", "none", "listed"] = Field(
        default="unknown",
        description="'unknown' = empty list, nobody said; 'none' = confirmed none; 'listed'.",
    )
    dietary: tuple[str, ...]
    default_size: SizeCodeStr | None
    ingredients_text: str | None
    photo_url: str | None
    photo_is_own: bool = Field(description="False when the photo is the ops menu item's.")
    badge: str | None
    sort_order: int
    visible: bool
    available: bool
    featured: bool
    ops_active: bool = Field(description="At least one active `menu_item` row carries this name.")
    from_price_pence: int | None
    sizes: tuple[ProductSizeOut, ...]
    option_group_ids: tuple[int, ...] = Field(description="Explicit attachments only.")
    effective_option_group_ids: tuple[int, ...] = Field(
        description="Explicit attachments plus groups applying to the category."
    )
    updated_at: datetime | None


class EffectiveGroupOut(Out):
    id: int
    name: str
    kind: OptionKindStr


class ProductByMenuItemOut(ProductAdminOut):
    """`ProductAdmin` for the Menu item page, with the option groups spelled out."""

    menu_item_id: int
    effective_option_groups: tuple[EffectiveGroupOut, ...]
    shop_url_path: str = Field(description='"/order/p/<slug>" on the public site.')


class ProductIn(In):
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=600)
    note: str | None = Field(default=None, max_length=300)
    kcal: int | None = Field(default=None, ge=0, le=9999)
    kcal_by_size: dict[SizeCodeStr, int] | None = None
    nutrition: dict[str, str] | None = None
    allergens: list[str] | None = None
    dietary: list[str] | None = None
    ingredients_text: str | None = Field(default=None, max_length=600)
    badge: str | None = Field(default=None, max_length=30)
    visible: bool | None = None
    available: bool | None = None
    featured: bool | None = None
    category_ops_name: str | None = Field(default=None, max_length=80)
    sort_order: int | None = Field(default=None, ge=0)
    default_size: SizeCodeStr | None = None
    option_group_ids: list[int] | None = None


class ProductsOrderIn(In):
    category_slug: str = Field(min_length=1, max_length=80)
    ids: list[int] = Field(min_length=1)


class ProductsBulkIn(In):
    ids: list[int] = Field(min_length=1)
    available: bool | None = None
    visible: bool | None = None


class BulkOut(Out):
    changed: int
    products: tuple[ProductAdminOut, ...]


class OptionIn(In):
    id: int | None = None
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=120)
    price_delta_pence: int = Field(default=0, ge=-10000, le=10000)
    kcal: int | None = Field(default=None, ge=0, le=9999)
    is_default: bool = False
    available: bool = True
    modifier_id: int | None = None
    sort_order: int = Field(default=0, ge=0)


class OptionAdminOut(Out):
    id: int
    name: str
    description: str | None
    price_delta_pence: int
    kcal: int | None
    is_default: bool
    available: bool
    sort_order: int
    modifier_id: int | None
    modifier_name: str | None
    photo_url: str | None


class OptionGroupIn(In):
    name: str = Field(min_length=1, max_length=80)
    prompt: str | None = Field(default=None, max_length=160)
    kind: OptionKindStr
    layout: OptionLayoutStr
    required: bool = False
    min_select: int = Field(default=0, ge=0)
    max_select: int | None = Field(default=None, ge=1)
    collapsed: bool = False
    applies_to_categories: list[str] = Field(default_factory=list)
    active: bool = True
    sort_order: int | None = Field(default=None, ge=0)
    options: list[OptionIn] = Field(default_factory=list)


class OptionGroupAdminOut(Out):
    id: int
    name: str
    prompt: str | None
    kind: OptionKindStr
    layout: OptionLayoutStr
    required: bool
    min_select: int
    max_select: int | None
    collapsed: bool
    applies_to_categories: tuple[str, ...]
    sort_order: int
    active: bool
    options: tuple[OptionAdminOut, ...]
    product_ids: tuple[int, ...] = Field(description="Products explicitly attached.")
    updated_at: datetime | None


class UpsellIn(In):
    placement: UpsellPlacementStr
    heading: str = Field(min_length=1, max_length=80)
    product_ids: list[int] = Field(default_factory=list)


class UpsellPatchIn(In):
    """Any subset, for PUT on an existing upsell."""

    placement: UpsellPlacementStr | None = None
    heading: str | None = Field(default=None, min_length=1, max_length=80)
    product_ids: list[int] | None = None
    sort_order: int = Field(default=0, ge=0)
    active: bool = True


class UpsellAdminOut(Out):
    id: int
    placement: UpsellPlacementStr
    heading: str
    product_ids: tuple[int, ...]
    sort_order: int
    active: bool


class BannerIn(In):
    title: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=300)
    link_href: str | None = Field(default=None, max_length=300)
    active: bool = True
    sort_order: int = Field(default=0, ge=0)
    starts_on: date | None = None
    ends_on: date | None = None


class BannerAdminOut(Out):
    id: int
    title: str
    subtitle: str | None
    photo_url: str | None
    link_href: str | None
    active: bool
    sort_order: int
    starts_on: date | None
    ends_on: date | None


class OpsOut(Out):
    items_without_category: int
    menu_items_active: int


class CatalogueAdminOut(Out):
    categories: tuple[CategoryAdminOut, ...]
    products: tuple[ProductAdminOut, ...]
    option_groups: tuple[OptionGroupAdminOut, ...]
    upsells: tuple[UpsellAdminOut, ...]
    banners: tuple[BannerAdminOut, ...]
    ops: OpsOut
    allergens: tuple[str, ...] = Field(description="The vocabulary (`domain/shop.py`).")
    dietary: tuple[str, ...]


class ModifierOut(Out):
    id: int
    name: str
    price_pence: int


class DeletedOut(Out):
    id: int
    deleted: bool


# ==========================================================================
# Insights (owner addition, CONTRACT §10.B)
# ==========================================================================


class InsightsPeriodOut(Out):
    from_: date = Field(alias="from")
    to: date
    days: int


class InsightsPreviousOut(Out):
    from_: date = Field(alias="from")
    to: date


class InsightsVsPreviousOut(Out):
    """Percent change against the previous window; `null` when it had no orders."""

    orders_pct: float | None
    revenue_pct: float | None
    avg_basket_pct: float | None


class InsightsFiguresOut(Out):
    orders: int = Field(description="Everything placed (every status but PENDING_PAYMENT).")
    revenue_pence: int = Field(description="COLLECTED orders only.")
    avg_basket_pence: int | None = Field(description="Revenue over collected orders; null if none.")
    items: int = Field(description="Line units on COLLECTED orders.")
    members_share: float | None = Field(description="% of placed orders from a Rewards member.")
    online_paid_share: float | None = Field(description="% of placed orders paid online.")
    cancelled: int
    rejected: int
    avg_minutes_to_ready: float | None = Field(description="placed_at → ready_at; null if none.")
    vs_previous: InsightsVsPreviousOut


class InsightsDayOut(Out):
    date: date
    orders: int
    revenue_pence: int
    cancelled: int = Field(description="Cancelled + rejected that day.")


class InsightsHourOut(Out):
    hour: int = Field(ge=0, le=23, description="Local hour of requested_at.")
    orders: int


class InsightsWeekdayOut(Out):
    weekday: int = Field(ge=0, le=6, description="Mon=0 … Sun=6.")
    orders: int
    revenue_pence: int


class InsightsProductOut(Out):
    product_id: int | None
    name: str
    qty: int
    revenue_pence: int


class InsightsOptionOut(Out):
    group: str
    name: str
    qty: int


class InsightsDiningOut(Out):
    takeaway: int
    eat_in: int


class InsightsPaymentOut(Out):
    counter: int
    online: int


class InsightsStatusNowOut(Out):
    new: int
    accepted: int
    preparing: int
    ready: int


class ShopInsightsOut(Out):
    period: InsightsPeriodOut
    previous: InsightsPreviousOut
    figures: InsightsFiguresOut
    per_day: tuple[InsightsDayOut, ...]
    by_hour: tuple[InsightsHourOut, ...]
    by_weekday: tuple[InsightsWeekdayOut, ...]
    top_products: tuple[InsightsProductOut, ...]
    top_options: tuple[InsightsOptionOut, ...]
    dining: InsightsDiningOut
    payment: InsightsPaymentOut
    status_now: InsightsStatusNowOut
    caveats: tuple[str, ...]
