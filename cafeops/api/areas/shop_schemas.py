"""Request and response models for the public ordering API (docs/shop/CONTRACT.md §4).

Served on the public domain through Caddy, so every request body forbids unknown fields
(`In`) and every string has a ceiling: a checkout form is an endpoint anybody on the
internet can write to. Money is integer pence; times are ISO-8601 UTC with offset and
`*_local` strings are Europe/London for display.
"""

from __future__ import annotations

from datetime import date as Date  # `SlotsOut.date` is a field name; see there
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from cafeops.api.schemas import In, Out
from cafeops.services.shop.push import endpoint_refusal

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


class DiningOut(Out):
    takeaway: bool
    eat_in: bool
    default: Literal["takeaway", "eat_in"]


class PayOut(Out):
    counter: bool
    online: bool
    provider: str | None = Field(description="The online provider key when `online` is true.")


class CafeOut(Out):
    name: str
    address_line: str
    postcode: str
    phone: str


class LoyaltyBlurbOut(Out):
    program_name: str
    stamps_required: int
    reward_text: str


class PushConfigOut(Out):
    enabled: bool
    vapid_public_key: str | None


class UpdatesOut(Out):
    """Which customer-update channels actually work: the setting is on AND the channel
    is configured on the server (§3c). The checkout shows only what can happen."""

    email: bool
    push: bool
    sms: bool


class ConfigOut(Out):
    enabled: bool
    closed_message: str
    hero_title: str
    hero_subtitle: str
    dining: DiningOut
    pay: PayOut
    kcal_notice: str
    allergen_notice: str
    collection_note: str
    terms_url: str
    open_now: bool
    next_open_local: str | None
    cafe: CafeOut
    loyalty: LoyaltyBlurbOut
    push: PushConfigOut
    require_account: bool = Field(
        description="§10.J: placing an order needs a signed-in Rewards card "
        "(`POST /orders` answers 401 sign_in_required without one). Quote stays open."
    )
    sms_notify: Literal["off", "ready", "all"] = Field(
        description="The SMS setting, reported as 'off' when Twilio is not configured."
    )
    updates: UpdatesOut


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------


class BannerOut(Out):
    id: int
    title: str
    subtitle: str | None
    photo_url: str | None
    link_href: str | None


class CategoryOut(Out):
    id: int
    slug: str
    name: str
    blurb: str | None
    photo_url: str | None
    photo_is_fallback: bool = Field(
        default=False,
        description="True when the tile borrows the first visible product's photo.",
    )
    product_count: int


class SizeOut(Out):
    menu_item_id: int
    code: Literal["S", "M", "XL", "ONE"]
    label: str
    price_pence: int
    kcal: int | None


class OptionOut(Out):
    id: int
    name: str
    description: str | None
    price_delta_pence: int
    kcal: int | None
    is_default: bool
    available: bool
    photo_url: str | None


class OptionGroupOut(Out):
    id: int
    name: str
    prompt: str | None
    kind: Literal["single", "multi"]
    layout: Literal["tiles", "photo_tiles", "checklist"]
    required: bool
    min_select: int
    max_select: int | None
    collapsed: bool
    options: list[OptionOut]


class UpsellOut(Out):
    heading: str
    product_ids: list[int]


class ProductOut(Out):
    id: int
    slug: str
    name: str
    category_slug: str | None
    description: str | None
    note: str | None
    kcal: int | None
    kcal_by_size: dict[str, Any] | None
    nutrition: dict[str, Any] | None
    allergens: list[str]
    allergens_state: Literal["unknown", "none", "listed"] = Field(
        description="'unknown' = nobody has said (empty list); 'none' = confirmed none; "
        "'listed' = see `allergens`. Never render 'unknown' as 'no allergens'."
    )
    dietary: list[str]
    ingredients_text: str | None
    photo_url: str | None
    badge: str | None
    available: bool
    featured: bool
    from_price_pence: int
    default_size: Literal["S", "M", "XL", "ONE"]
    sizes: list[SizeOut]
    option_groups: list[OptionGroupOut]
    upsells: list[UpsellOut]


class CatalogueOut(Out):
    generated_at: datetime
    version: str = Field(description="Hash of the payload; the client caches on it.")
    banners: list[BannerOut]
    categories: list[CategoryOut]
    products: list[ProductOut]
    basket_upsells: list[UpsellOut]


# --------------------------------------------------------------------------
# slots
# --------------------------------------------------------------------------


class AsapOut(Out):
    available: bool
    at: datetime | None
    local: str | None


class SlotOut(Out):
    at: datetime
    local: str
    available: bool


class SlotsOut(Out):
    # The JSON field is `date` (the shop app reads it), which inside this class body
    # shadows the type of the same name -- hence the aliased import at the top.
    date: Date
    open: bool
    reason: str | None
    asap: AsapOut
    slots: list[SlotOut]
    days: list[Date]


# --------------------------------------------------------------------------
# quote and orders
# --------------------------------------------------------------------------


class LineIn(In):
    product_id: int = Field(ge=1)
    menu_item_id: int = Field(ge=1)
    qty: int = Field(ge=1, le=20)
    option_ids: list[int] = Field(default_factory=list, max_length=20)


class QuoteIn(In):
    dining: Literal["takeaway", "eat_in"] = "takeaway"
    lines: list[LineIn] = Field(max_length=30)
    reward: bool = False


class QuotedOptionOut(Out):
    group: str
    name: str
    price_delta_pence: int


class QuotedLineOut(Out):
    product_id: int
    menu_item_id: int
    qty: int
    option_ids: list[int]
    name: str
    size_label: str
    unit_price_pence: int
    line_total_pence: int
    options: list[QuotedOptionOut]
    problems: list[str]


class RewardOut(Out):
    applied: bool
    line_index: int | None
    text: str | None


class QuoteOut(Out):
    lines: list[QuotedLineOut]
    subtotal_pence: int
    discount_pence: int
    total_pence: int
    reward: RewardOut
    problems: list[str]


class CustomerIn(In):
    name: str = Field(default="", max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)


class PlaceOrderIn(In):
    dining: Literal["takeaway", "eat_in"] = "takeaway"
    asap: bool = True
    requested_at: datetime | None = None
    lines: list[LineIn] = Field(max_length=30)
    reward: bool = False
    customer: CustomerIn
    note: str | None = Field(default=None, max_length=300)
    #: Eat in only: the table number or name. Ignored for takeaway.
    table: str | None = Field(default=None, max_length=20)
    allergy_ack: bool
    payment: Literal["counter", "online"] = "counter"
    expected_total_pence: int = Field(ge=0)
    #: §3c: text me about this order (SMS is paid; needs a phone number).
    sms_opt_in: bool = False
    #: Honeypot. People never see it; form-filling bots do.
    website: str = Field(default="", max_length=200)


class PaymentOut(Out):
    method: Literal["counter", "online"]
    status: Literal["unpaid", "paid", "refunded", "failed"]
    checkout_url: str | None = None


class PlacedOut(Out):
    code: str
    status: str
    access_token: str
    total_pence: int
    requested_at: datetime
    requested_local: str
    payment: PaymentOut


class OrderLineOut(Out):
    name: str
    size_label: str
    qty: int
    unit_price_pence: int
    line_total_pence: int
    options: list[QuotedOptionOut]


class OrderEventOut(Out):
    at: datetime
    kind: str
    detail: str | None


class OrderCustomerOut(Out):
    name: str


class OrderNotifyOut(Out):
    email: bool
    push_subscribed: bool
    sms: bool


class OrderOut(Out):
    code: str
    status: str
    status_label: str
    status_step: int = Field(ge=0, le=4)
    dining: Literal["takeaway", "eat_in"]
    table: str | None = None
    asap: bool
    requested_at: datetime
    requested_local: str
    placed_at: datetime
    ready_at: datetime | None
    collected_at: datetime | None
    customer: OrderCustomerOut
    lines: list[OrderLineOut]
    subtotal_pence: int
    discount_pence: int
    total_pence: int
    payment: PaymentOut
    collection_note: str
    cancel_allowed: bool
    notify: OrderNotifyOut
    events: list[OrderEventOut]


# --------------------------------------------------------------------------
# me
# --------------------------------------------------------------------------


class MeRewardOut(Out):
    id: int
    text: str


class RecentLineOut(Out):
    """A basket line the client can put straight back in the basket ("order again")."""

    product_id: int
    menu_item_id: int
    qty: int
    option_ids: list[int]


class RecentOrderOut(Out):
    code: str
    status: str
    placed_at: datetime
    total_pence: int
    lines: list[RecentLineOut] = Field(
        default_factory=list,
        description="Only lines whose product, size and options are still in the catalogue.",
    )
    reorder_complete: bool = Field(
        default=True, description="False when a line was dropped because something vanished."
    )


class SavedMethodOut(Out):
    label: str
    ref: str
    is_default: bool


class MePaymentOut(Out):
    provider: str
    saved_methods: list[SavedMethodOut]


class MemberOut(Out):
    """The member's own profile (§10.J). What `PATCH /me` edits."""

    first_name: str
    email: str | None
    phone: str | None
    birthday_day: int | None
    birthday_month: int | None
    marketing_opt_in: bool
    member_since: datetime


class MeOut(Out):
    first_name: str
    email: str | None
    phone: str | None
    card_id: str
    stamps_current: int
    stamps_required: int
    reward: MeRewardOut | None
    recent_orders: list[RecentOrderOut]
    payment: MePaymentOut | None
    member: MemberOut


class MeOrderOptionOut(Out):
    group: str
    name: str


class MeOrderLineOut(Out):
    name: str
    size_label: str
    qty: int
    options: list[MeOrderOptionOut]


class ReorderOut(Out):
    lines: list[RecentLineOut] = Field(
        description="Only lines whose product, size and options are still in the catalogue."
    )
    complete: bool = Field(description="False when a line was dropped.")


class MeOrderOut(Out):
    """One of the member's past orders (§10.J), newest first on `/me/orders`."""

    code: str
    status: str
    status_label: str
    placed_at: datetime
    placed_local: str
    requested_local: str
    dining: Literal["takeaway", "eat_in"]
    table: str | None
    total_pence: int
    lines: list[MeOrderLineOut]
    reorder: ReorderOut


class MeOrdersOut(Out):
    items: list[MeOrderOut]
    total: int
    page: int
    page_size: int


class MePatchIn(In):
    """Any subset of the member's profile; a field left out is left alone. `email` /
    `phone` sent as null clear that contact (one of the two must remain);
    `birthday_day` and `birthday_month` go together, both null removes the birthday."""

    first_name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    birthday_day: int | None = Field(default=None, ge=1, le=31)
    birthday_month: int | None = Field(default=None, ge=1, le=12)
    marketing_opt_in: bool | None = None


class PushKeysIn(In):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscribeIn(In):
    """`endpoint` must be a real browser push service (FCM, Apple, Mozilla, WNS): the
    server POSTs to it later, so anything else is a request forgery waiting to happen
    (`services/shop/push.endpoint_refusal`)."""

    endpoint: str = Field(min_length=12, max_length=500, pattern=r"^https://")
    keys: PushKeysIn

    @field_validator("endpoint")
    @classmethod
    def _push_service_only(cls, value: str) -> str:
        refusal = endpoint_refusal(value)
        if refusal is not None:
            raise ValueError(refusal)
        return value


class PushSubscribedOut(Out):
    subscribed: bool
    devices: int


class WebhookOut(Out):
    received: bool
    kind: str
    detail: str
