"""Order online (click & collect): the shop's settings, catalogue overlay and orders.

docs/shop/CONTRACT.md §2 (binding). The shapes that matter:

- **The catalogue is an overlay on the ops menu, never a copy of it.** `shop_category`
  is keyed to `menu_category.name` and `shop_product` to `menu_item.name`; sizes and
  prices are always read live from `menu_item`. The shop tables carry only what the
  customer sees and the till does not know: blurbs, kcal, allergens, photos, badges,
  "sold out today". Sync (`services/shop/catalog`) inserts rows for new menu names and
  never deletes one, so an admin's copy survives the item being retired and revived.
- **Size is not an option group.** Sizes are the `menu_item` rows sharing a name; an
  option group is everything else a customer picks (milk, extras, customise).
- **An order is a snapshot.** Every line stores the name, size label, unit price and
  option summary as they were at placement, because the menu will change and the
  customer's receipt must not.
- **A collected order is a sale** (§3.1): moving to COLLECTED writes one `sale` row per
  line with `channel=WEB, source=ONLINE`; `sale_receipt_id` (`web:<code>`) guards
  against writing them twice. Nothing here writes `stock_movement` (invariant 12).
- **Every status change is a `shop_order_event` row** (§3.8).

Enum columns are `enum_col(...)` VARCHAR: adding a member is code only.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow

if TYPE_CHECKING:
    from cafeops.db.models.media import MediaAsset


# --------------------------------------------------------------------------
# enums (CONTRACT §2)
# --------------------------------------------------------------------------


class DiningOption(enum.Enum):
    TAKEAWAY = "TAKEAWAY"
    EAT_IN = "EAT_IN"


class OptionKind(enum.Enum):
    SINGLE = "SINGLE"
    MULTI = "MULTI"


class OptionLayout(enum.Enum):
    #: The name + price/kcal tile.
    TILES = "TILES"
    #: The coffee-bean style image card with name and tagline.
    PHOTO_TILES = "PHOTO_TILES"
    #: The "Customise" checkbox rows.
    CHECKLIST = "CHECKLIST"


class UpsellPlacement(enum.Enum):
    ITEM_PAGE = "ITEM_PAGE"
    BASKET = "BASKET"


class OrderStatus(enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PREPARING = "PREPARING"
    READY = "READY"
    COLLECTED = "COLLECTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ShopPaymentMethod(enum.Enum):
    """The contract calls this `PaymentMethod`; `enums.PaymentMethod` (finance: how the
    day's takings arrived) already owns that name, so the shop's carries a prefix.
    `PaymentMethod` below is an alias for code written against the contract's name."""

    COUNTER = "COUNTER"
    ONLINE = "ONLINE"


PaymentMethod = ShopPaymentMethod


class PaymentStatus(enum.Enum):
    UNPAID = "UNPAID"
    PAID = "PAID"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


class ShopSettings(Base):
    """One row, `id = 1`, created by the migration (CONTRACT §2.1)."""

    __tablename__ = "shop_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Off: the shop shows `closed_message` and takes no orders.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    closed_message: Mapped[str] = mapped_column(String(300), nullable=False)
    hero_title: Mapped[str] = mapped_column(String(120), nullable=False)
    hero_subtitle: Mapped[str] = mapped_column(String(300), nullable=False)
    takeaway_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    eat_in_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_dining: Mapped[DiningOption] = mapped_column(
        enum_col(DiningOption), nullable=False, default=DiningOption.TAKEAWAY
    )
    #: Earliest collection = now + lead, rounded up to the next slot.
    lead_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    slot_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    #: 0 = unlimited.
    max_orders_per_slot: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    #: How many days ahead a customer may schedule (0 = today only).
    days_ahead: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: `[{"weekday": 0, "open": "09:00", "close": "19:00"}, ...]` Mon=0..Sun=6; a missing
    #: weekday is closed. The shop's OWN hours, not the café's (§3.4).
    hours: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    last_order_minutes_before_close: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20
    )
    #: `[{"date": "2026-12-25", "note": "Christmas"}]`.
    closures: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    pay_at_counter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Only honoured when Stripe is configured (`settings.stripe_secret_key`).
    pay_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kcal_notice: Mapped[str] = mapped_column(String(200), nullable=False)
    allergen_notice: Mapped[str] = mapped_column(Text, nullable=False)
    collection_note: Mapped[str] = mapped_column(String(300), nullable=False)
    terms_url: Mapped[str] = mapped_column(String(300), nullable=False, default="/privacy")
    #: Collected orders stamp the member's card (§3.5).
    loyalty_stamps_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: CONTRACT §3b: which `integrations/payments/providers` key takes online payment
    #: ("stripe" | "lightspeed"). Honoured only when that provider is configured.
    payment_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="stripe", server_default="stripe"
    )
    #: CONTRACT §3b: which `integrations/pos` sink receives a NEW order ("none" |
    #: "lightspeed").
    pos_sink: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default="none"
    )
    #: CONTRACT §3c: the free channels are on by default; SMS costs ~4p a text, so
    #: "ready" sends one per order, "all" every status change, "off" none.
    email_notify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    push_notify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    sms_notify: Mapped[str] = mapped_column(
        String(12), nullable=False, default="ready", server_default="ready"
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("lead_minutes >= 0", name="shop_lead_non_negative"),
        CheckConstraint("slot_minutes >= 1", name="shop_slot_positive"),
        CheckConstraint("max_orders_per_slot >= 0", name="shop_capacity_non_negative"),
        CheckConstraint("days_ahead >= 0", name="shop_days_ahead_non_negative"),
    )


# --------------------------------------------------------------------------
# catalogue overlay
# --------------------------------------------------------------------------


class ShopBanner(Base):
    """The promo carousel (CONTRACT §2.2)."""

    __tablename__ = "shop_banner"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(300))
    photo_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL")
    )
    #: A shop path (`/order/c/hot-matcha`) or a site page.
    link_href: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)

    photo: Mapped[MediaAsset | None] = relationship()


class ShopCategory(Base):
    """Keyed to `menu_category.name` (CONTRACT §2.3). Never deleted by sync."""

    __tablename__ = "shop_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    ops_name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    #: Display name; defaults to the ops name.
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    blurb: Mapped[str | None] = mapped_column(String(300))
    photo_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    photo: Mapped[MediaAsset | None] = relationship()


class ShopProduct(Base):
    """One per product NAME -- the ops `menu_item.name`; its sizes are the `menu_item`
    rows with that name (CONTRACT §2.4). Upserted by sync, never deleted."""

    __tablename__ = "shop_product"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    #: From `menu_item.category` at sync; the admin may move it.
    category_ops_name: Mapped[str | None] = mapped_column(String(80))
    #: Override of the display name.
    display_name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(600))
    kcal: Mapped[int | None] = mapped_column(Integer)
    #: `domain/shop.py: ALLERGENS` vocabulary.
    allergens: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: `domain/shop.py: DIETARY` vocabulary.
    dietary: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: The size code the item page pre-selects; NULL = the cheapest.
    default_size: Mapped[str | None] = mapped_column(String(4))
    ingredients_text: Mapped[str | None] = mapped_column(String(600))
    #: An italic caveat under the description.
    note: Mapped[str | None] = mapped_column(String(300))
    #: `{"S": 244, "M": 339, "XL": 420}`.
    kcal_by_size: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    #: Strings, shown on a Nutrition tab; a missing value renders "--", never 0.
    nutrition: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    #: Falls back to `menu_item.photo_asset_id`.
    photo_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL")
    )
    #: "New", "Back for autumn".
    badge: Mapped[str | None] = mapped_column(String(30))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: False = sold out today, shown greyed "Sold out".
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    photo: Mapped[MediaAsset | None] = relationship()
    option_group_links: Mapped[list[ShopProductOptionGroup]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_shop_product_category", "category_ops_name", "sort_order"),)


class ShopOptionGroup(Base):
    """ "Milk", "Extras", "Customise" (CONTRACT §2.5). Size is NOT an option group."""

    __tablename__ = "shop_option_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: "Choose your milk".
    prompt: Mapped[str | None] = mapped_column(String(160))
    kind: Mapped[OptionKind] = mapped_column(enum_col(OptionKind), nullable=False)
    layout: Mapped[OptionLayout] = mapped_column(enum_col(OptionLayout), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_select: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_select: Mapped[int | None] = mapped_column(Integer)
    #: Black Sheep's "Add extras and syrups": shown folded until tapped.
    collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: `shop_category.slug` list; empty = only explicit attachments.
    applies_to_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    options: Mapped[list[ShopOption]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="ShopOption.sort_order, ShopOption.id",
    )

    __table_args__ = (
        CheckConstraint("min_select >= 0", name="shop_option_group_min_non_negative"),
        CheckConstraint(
            "max_select IS NULL OR max_select >= 1", name="shop_option_group_max_positive"
        ),
    )


class ShopOption(Base):
    __tablename__ = "shop_option"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("shop_option_group.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The tagline under a tile: "Double the caffeine!".
    description: Mapped[str | None] = mapped_column(String(120))
    price_delta_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kcal: Mapped[int | None] = mapped_column(Integer)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: When set, the ops modifier id goes into `sale.applied_modifiers` at COLLECTED so
    #: stock depletes the right milk or syrup.
    modifier_id: Mapped[int | None] = mapped_column(ForeignKey("modifier.id", ondelete="SET NULL"))
    photo_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL")
    )

    group: Mapped[ShopOptionGroup] = relationship(back_populates="options")
    photo: Mapped[MediaAsset | None] = relationship()

    __table_args__ = (Index("ix_shop_option_group", "group_id", "sort_order"),)


class ShopProductOptionGroup(Base):
    """Explicit attachment of a group to a product. Effective groups for a product are
    these plus every group whose `applies_to_categories` names the product's category."""

    __tablename__ = "shop_product_option_group"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("shop_product.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("shop_option_group.id", ondelete="CASCADE"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    product: Mapped[ShopProduct] = relationship(back_populates="option_group_links")
    group: Mapped[ShopOptionGroup] = relationship()


class ShopUpsell(Base):
    """ "Fancy a pastry?" rows on the item page or in the basket (CONTRACT §2.6)."""

    __tablename__ = "shop_upsell"

    id: Mapped[int] = mapped_column(primary_key=True)
    placement: Mapped[UpsellPlacement] = mapped_column(enum_col(UpsellPlacement), nullable=False)
    heading: Mapped[str] = mapped_column(String(80), nullable=False)
    #: `shop_product.id` list.
    product_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------


class ShopOrder(Base):
    """One click-and-collect order (CONTRACT §2.7). Lines and events cascade with it;
    a `sale` written at COLLECTED does not -- it is the till's record, not the shop's."""

    __tablename__ = "shop_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 6 chars from `domain.shop.CODE_ALPHABET`, shown as `SC-XXXXXX`.
    code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    status: Mapped[OrderStatus] = mapped_column(enum_col(OrderStatus), nullable=False)
    dining: Mapped[DiningOption] = mapped_column(enum_col(DiningOption), nullable=False)
    asap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The slot start; for ASAP the computed one.
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    collected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(300))
    #: "customer" / "staff".
    cancelled_by: Mapped[str | None] = mapped_column(String(40))
    customer_name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: E.164.
    customer_phone: Mapped[str | None] = mapped_column(String(20))
    customer_email: Mapped[str | None] = mapped_column(String(254))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_member.id"))
    card_id: Mapped[str | None] = mapped_column(ForeignKey("loyalty_card.id"))
    #: The customer's note.
    note: Mapped[str | None] = mapped_column(String(300))
    #: Eat-in only: the table the customer is sitting at ("4", "window").
    table: Mapped[str | None] = mapped_column(String(20))
    allergy_ack: Mapped[bool] = mapped_column(Boolean, nullable=False)
    subtotal_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The reward's discount.
    discount_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: A free drink used on this order; redeemed at COLLECTED, released on cancel.
    reward_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_reward.id"))
    payment_method: Mapped[ShopPaymentMethod] = mapped_column(
        enum_col(ShopPaymentMethod), nullable=False
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        enum_col(PaymentStatus), nullable=False, default=PaymentStatus.UNPAID
    )
    #: Stripe checkout session id.
    payment_ref: Mapped[str | None] = mapped_column(String(120))
    #: The provider's payment (Stripe `payment_intent`), stored at webhook time so a
    #: refund needs no second lookup of the session.
    payment_intent: Mapped[str | None] = mapped_column(String(120))
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: `web:<code>`, written at COLLECTED. The guard against writing the sale twice.
    sale_receipt_id: Mapped[str | None] = mapped_column(String(80))
    stamp_event_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_stamp_event.id"))
    #: The customer's status-page bearer.
    access_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    staff_note: Mapped[str | None] = mapped_column(String(300))
    #: CONTRACT §3b: the POS's reference for the pushed order (K-Series
    #: `thirdPartyReference` we sent, or the account identifier its webhook returned).
    pos_ref: Mapped[str | None] = mapped_column(String(80))
    #: CONTRACT §3c: the customer asked for texts about this order (paid channel).
    sms_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    lines: Mapped[list[ShopOrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="ShopOrderLine.sort_order, ShopOrderLine.id",
    )
    events: Mapped[list[ShopOrderEvent]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="ShopOrderEvent.at, ShopOrderEvent.id",
    )

    __table_args__ = (
        CheckConstraint(
            "customer_phone IS NOT NULL OR customer_email IS NOT NULL",
            name="shop_order_has_contact",
        ),
        CheckConstraint("subtotal_pence >= 0", name="shop_order_subtotal_non_negative"),
        CheckConstraint("discount_pence >= 0", name="shop_order_discount_non_negative"),
        CheckConstraint("total_pence >= 0", name="shop_order_total_non_negative"),
        Index("ix_shop_order_status", "status"),
        Index("ix_shop_order_requested_at", "requested_at"),
        Index("ix_shop_order_placed_at", "placed_at"),
        Index("ix_shop_order_member", "member_id"),
    )

    def __repr__(self) -> str:
        return f"<ShopOrder {self.id} {self.code} {self.status.value}>"


class ShopOrderLine(Base):
    __tablename__ = "shop_order_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("shop_order.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("shop_product.id", ondelete="SET NULL")
    )
    #: The size row sold.
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_item.id"), nullable=False)
    #: Snapshot at placement.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: "Small" / "Medium" / "Large" / "".
    size_label: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Size price + option deltas.
    unit_price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `[{"group": "Milk", "name": "Oat milk", "price_delta_pence": 50, "modifier_id": 3}]`.
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    line_total_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    order: Mapped[ShopOrder] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("qty >= 1", name="shop_order_line_qty_positive"),
        Index("ix_shop_order_line_order", "order_id"),
    )


class ShopOrderEvent(Base):
    """What happened to an order and when. Append-only."""

    __tablename__ = "shop_order_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("shop_order.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: placed, paid, accepted, preparing, ready, collected, cancelled, rejected, note,
    #: notified, stamped, sale_recorded.
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(400))
    #: "customer", "staff:<name>", "system", "stripe".
    actor: Mapped[str] = mapped_column(String(80), nullable=False)

    order: Mapped[ShopOrder] = relationship(back_populates="events")

    __table_args__ = (Index("ix_shop_order_event_order", "order_id", "at"),)


class ShopPushSubscription(Base):
    """A browser's Web Push subscription for one order's status (CONTRACT §3c).

    Per ORDER, not per member: the status page subscribes whoever is looking at it,
    signed in or not, and the subscription dies with the order. A 404/410 from the push
    service sets `expired_at`."""

    __tablename__ = "shop_push_subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("shop_order.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_shop_push_subscription_order", "order_id"),)


class ShopPaymentCustomer(Base):
    """The member's account at a payment provider (CONTRACT §3b.4). Never a card
    number: only the provider's customer reference and a label ("Visa ---- 4242")."""

    __tablename__ = "shop_payment_customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("loyalty_member.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    default_method_label: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("member_id", "provider", name="uq_shop_payment_customer_member"),
    )


__all__ = [
    "DiningOption",
    "OptionKind",
    "OptionLayout",
    "OrderStatus",
    "PaymentMethod",
    "PaymentStatus",
    "ShopBanner",
    "ShopCategory",
    "ShopOption",
    "ShopOptionGroup",
    "ShopOrder",
    "ShopOrderEvent",
    "ShopOrderLine",
    "ShopPaymentCustomer",
    "ShopPaymentMethod",
    "ShopProduct",
    "ShopProductOptionGroup",
    "ShopPushSubscription",
    "ShopSettings",
    "ShopUpsell",
    "UpsellPlacement",
]
