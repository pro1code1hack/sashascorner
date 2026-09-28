"""Sasha's Corner Rewards: programmes, members, cards, the stamp ledger and rewards.

docs/loyalty/CONTRACT.md §2 (binding) and SPEC.md. The shapes that matter:

- **One customer, one card, one serial.** `loyalty_card.id` is a uuid4 string and is the
  Apple pass serial, the Google object suffix and the `/c/<id>` of the web card. It never
  changes -- recovering a lost phone re-downloads the same card.
- **The stamp ledger is append-only.** `stamps_current` is a cache; the truth is the
  sequence of `loyalty_stamp_event` rows. An undo is a new row with reason UNDO and the
  opposite delta, pointing at what it undoes, exactly as the stock ledger's corrections
  are ADJUSTMENT movements (invariant 12 by analogy).
- **A reward row is the audit of a free drink.** Issuing one subtracts `stamps_required`
  from the card (carry-over stays); redeeming it stamps `redeemed_at` and, when staff say
  which drink it was, writes a £0 `sale` so stock and COGS stay honest.
- **Erasure keeps the ledger.** Deleting a member nulls every personal field and voids the
  card, but the stamp and reward rows stay -- they carry no PII, and the statistics of
  "how many free drinks did we give" should not change because somebody left.

Additions beyond the contract's sketch (listed in the agent's report): `web_seen_at` on the
card (so "which wallet" can say "web"), `referral_rewarded_at` on the member (the
once-per-referred-member rule), `source_event_id` on stamp events and `stamp_event_id` on
rewards (so an undo can find what a stamp caused), and the `loyalty_audit` table (fraud
alerts, consent changes and recovery links, all things somebody may later ask "when?").

Phase 3 (docs/loyalty/CONTRACT.md "Phase 3"): a programme has a `kind` (STAMPS, or
POINTS earned per pound spent), an eligibility filter and a reward catalogue
(`loyalty_reward_option`); a member may hold one card per programme. The ledger is the
same `loyalty_stamp_event` for both kinds -- on a POINTS card a "stamp" is a point, and
`stamps_required` is the points threshold -- so every append-only rule, undo and audit
above holds for points unchanged. Lightspeed receipts that name a customer are recorded
in `loyalty_pos_receipt` (ids and keyed hashes only), and what each receipt earned is in
`loyalty_pos_award`.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow


class StampReason(enum.Enum):
    PURCHASE = "PURCHASE"
    PAPER_MIGRATION = "PAPER_MIGRATION"
    MANUAL_FIX = "MANUAL_FIX"
    #: In the spec's enum; never written. Redeeming touches the reward row, not the
    #: ledger -- the stamps were consumed when the reward was issued (CONTRACT §2).
    REDEEM = "REDEEM"
    REFERRAL = "REFERRAL"
    UNDO = "UNDO"
    #: The programme's "first stamp is on us" at join (BACKOFFICE-V2 §4). Not a purchase.
    WELCOME = "WELCOME"


class RewardKind(enum.Enum):
    STAMP_CARD = "STAMP_CARD"
    BIRTHDAY = "BIRTHDAY"
    REFERRAL = "REFERRAL"


class OtpChannel(enum.Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    STAFF = "STAFF"


class ProgramKind(enum.Enum):
    #: One stamp per eligible item (the paper card).
    STAMPS = "STAMPS"
    #: Points per pound spent; the reward at `stamps_required` points.
    POINTS = "POINTS"


class PosAwardStatus(enum.Enum):
    #: The receipt's units are on the card (`stamp_event_id`).
    AWARDED = "AWARDED"
    #: A staff scan on the same card within the dedupe window already counted this visit.
    SKIPPED_STAFF_SCAN = "SKIPPED_STAFF_SCAN"
    #: Nothing to give: no eligible line, all voided or refunded, or before the member joined.
    NOTHING = "NOTHING"
    #: Frozen: a reversal was due but refused (the reward it completed was already given),
    #: or staff undid the award by hand. The sync never touches this receipt again.
    KEPT = "KEPT"


class CampaignSegment(enum.Enum):
    ALL_OPTED_IN = "ALL_OPTED_IN"
    LAPSED_30 = "LAPSED_30"
    REWARD_READY = "REWARD_READY"
    NEW_30 = "NEW_30"
    #: Birthday in the current calendar month (local).
    BIRTHDAY = "BIRTHDAY"


class LoyaltyProgram(Base):
    """One row today (`slug = "stamp"`). The table exists so phase 3 can add a matcha club."""

    __tablename__ = "loyalty_program"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    stamps_required: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    max_stamps_per_scan: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    reward_text: Mapped[str] = mapped_column(String(120), nullable=False)
    #: NULL = any drink (CONTRACT §0). Pence, compared with `menu_item.price_pence`.
    reward_max_price_pence: Mapped[int | None] = mapped_column(Integer)
    birthday_reward: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Stamps the referrer gets when a referred member's card gets its first purchase
    #: stamp. 0 disables referrals.
    referral_stamps: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # --- phase 3 ------------------------------------------------------------------
    kind: Mapped[ProgramKind] = mapped_column(
        enum_col(ProgramKind), nullable=False, default=ProgramKind.STAMPS
    )
    #: POINTS only: points per whole pound. Points for a spend are
    #: `spend_pence * points_per_pound // 100` -- integer maths, rounded down.
    points_per_pound: Mapped[int | None] = mapped_column(Integer)
    #: What earns: `{"scope": "drinks"|"all", "categories": [...], "keywords": [...],
    #: "template_ids": [...]}` (`domain.loyalty.Eligibility`). NULL = drinks, as phase 1.
    eligibility: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: One line for the join page and the pass ("Every 6th matcha on us").
    description: Mapped[str | None] = mapped_column(String(200))
    #: The reward's name on the pass when one is ready ("Free drink ready").
    reward_ready_label: Mapped[str] = mapped_column(
        String(60), nullable=False, default="Free drink ready"
    )
    #: Order on the join page, the scanner and the back office.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # --- back-office settings (b7c1e0a10004) --------------------------------------
    #: Cooldown: more than this many purchase stamps on one card within
    #: `cooldown_minutes` needs a manager's PIN (SPEC: >3 in 10 minutes).
    cooldown_max_stamps: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    cooldown_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    #: The owner's 90-day targets, `{metric: number}` (SPEC "Exact targets are an open
    #: question" -- so they are the owner's to set). Shares are 0..1. NULL = none set.
    targets: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # --- loyalty card v2 (docs/loyalty/BACKOFFICE-V2.md) ------------------------------
    #: The sticker set: enabled sticker keys in scanner order. NULL = all eight, in the
    #: catalogue's order (`domain.loyalty.STICKER_KEYS`).
    stickers: Mapped[list[str] | None] = mapped_column(JSON)
    #: "The first stamp is on us when they join."
    welcome_stamp: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    #: Cards untouched this many months reset to zero. NULL = stamps never expire.
    stamps_expire_months: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("stamps_required >= 1", name="stamps_required_positive"),
        CheckConstraint(
            "(kind = 'POINTS') = (points_per_pound IS NOT NULL)", name="points_rate_iff_points"
        ),
        CheckConstraint("max_stamps_per_scan >= 1", name="max_per_scan_positive"),
        CheckConstraint("referral_stamps >= 0", name="referral_stamps_non_negative"),
    )


class LoyaltyMember(Base):
    """A customer. Personal data is the first name, one contact and an optional birthday.

    `email` and `phone` are UNIQUE: "one card per phone number or email" is enforced by the
    database, not by a lookup that two simultaneous joins could both pass.
    """

    __tablename__ = "loyalty_member"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Lower-cased.
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    #: E.164, UK default (+44).
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    birthday_day: Mapped[int | None] = mapped_column(Integer)
    birthday_month: Mapped[int | None] = mapped_column(Integer)
    #: When the birthday was entered or last changed -- the 30-day rule reads this.
    birthday_set_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: PECR: consent is recorded with when and where. Cleared on opt-out; the opt-out itself
    #: is a `loyalty_audit` row.
    opt_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    opt_in_source: Mapped[str | None] = mapped_column(String(60))
    #: The `?src=` the join page was opened with (table, till, cup, flyer-uni, ...).
    source: Mapped[str | None] = mapped_column(String(40))
    referred_by_member_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_member.id"))
    #: Set when the referrer was credited, so a referral pays once however the stamps go.
    referral_rewarded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    terms_accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: Phase 3: the Lightspeed K-Series customer (`consumer.id` on a sale) this member is.
    #: Set automatically when a receipt's customer email/phone matches exactly one member,
    #: or explicitly by staff. Receipts naming this customer stamp the member's cards.
    lightspeed_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    lightspeed_linked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: "auto_email" | "auto_phone" | "staff" | "back_office".
    lightspeed_link_source: Mapped[str | None] = mapped_column(String(20))
    #: Staff's own notes ("Oat flat white, no lid. Comes in Tuesdays."). Back office only.
    notes: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL OR deleted_at IS NOT NULL",
            name="has_contact",
        ),
        CheckConstraint(
            "(birthday_day IS NULL) = (birthday_month IS NULL)", name="birthday_complete"
        ),
        Index("ix_loyalty_member_activity", "last_activity_at"),
        Index("ix_loyalty_member_created", "created_at"),
    )


class LoyaltyCard(Base):
    __tablename__ = "loyalty_card"

    #: uuid4 -- the pass serial.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("loyalty_member.id"), nullable=False)
    program_id: Mapped[int] = mapped_column(ForeignKey("loyalty_program.id"), nullable=False)
    stamps_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cycles_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Cache of "an unredeemed, unvoided, unexpired reward exists". Recomputed by every
    #: service that touches a reward, and by the birthday job when one expires.
    reward_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Apple `authenticationToken` AND the web card's bearer. Random, url-safe.
    auth_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    qr_secret: Mapped[str] = mapped_column(String(32), nullable=False)
    #: First time the web card was opened. Written once, never refreshed: a write per page
    #: view would put every customer's phone on the single SQLite writer.
    web_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: The sticker in each filled slot (BACKOFFICE-V2 §2): one key per stamp, so its
    #: length equals `stamps_current` on a STAMPS card. A cache the stamp writer keeps;
    #: changing a slot's sticker edits only this (cosmetic -- the ledger is untouched).
    stickers: Mapped[list[str] | None] = mapped_column(JSON)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: Bumps on EVERY state change: Apple's "passes updated since" reads it.
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    member: Mapped[LoyaltyMember] = relationship()
    program: Mapped[LoyaltyProgram] = relationship()

    __table_args__ = (
        UniqueConstraint("member_id", "program_id", name="uq_loyalty_card_member_program"),
        CheckConstraint("stamps_current >= 0", name="stamps_non_negative"),
        Index("ix_loyalty_card_updated", "updated_at"),
    )


class LoyaltyStampEvent(Base):
    """APPEND-ONLY. `delta` is never zero; an undo is a new row, never an edit."""

    __tablename__ = "loyalty_stamp_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("loyalty_card.id"), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[StampReason] = mapped_column(enum_col(StampReason), nullable=False)
    note: Mapped[str | None] = mapped_column(String(400))
    staff_user_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("staff_device.id"))
    undoes_event_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_stamp_event.id"))
    #: The event that caused this one: a REFERRAL row points at the referred member's
    #: first purchase stamp, so undoing that stamp can undo the referral too.
    source_event_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_stamp_event.id"))
    #: The sticker this stamp put on the card (the first, for a +2 or +3). History only.
    sticker: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("delta <> 0", name="delta_non_zero"),
        CheckConstraint(
            "(reason = 'UNDO') = (undoes_event_id IS NOT NULL)", name="undo_names_target"
        ),
        Index("ix_loyalty_stamp_event_card", "card_id", "created_at"),
        Index("ix_loyalty_stamp_event_staff", "staff_user_id", "created_at"),
        Index("ix_loyalty_stamp_event_created", "created_at"),
    )


class LoyaltyReward(Base):
    __tablename__ = "loyalty_reward"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("loyalty_card.id"), nullable=False)
    kind: Mapped[RewardKind] = mapped_column(enum_col(RewardKind), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: NULL for a stamp-card reward: "reward doesn't expire while card active".
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    redeemed_menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_item.id"))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sale.id"))
    staff_user_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id"))
    #: The stamp event that filled the card, so undoing it can void this reward.
    stamp_event_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_stamp_event.id"))
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: BIRTHDAY only: the year celebrated. The once-a-year rule is the unique key below
    #: (NULLs are distinct, so stamp-card rewards are unaffected).
    birthday_year: Mapped[int | None] = mapped_column(Integer)
    #: Phase 3: which catalogue entry it was redeemed as ("Slice of cake"). NULL before
    #: phase 3 and when the programme has no catalogue.
    reward_option_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_reward_option.id"))

    __table_args__ = (
        UniqueConstraint("card_id", "kind", "birthday_year", name="uq_loyalty_reward_birthday"),
        CheckConstraint(
            "(kind = 'BIRTHDAY') = (birthday_year IS NOT NULL)", name="birthday_year_iff_birthday"
        ),
        Index("ix_loyalty_reward_card", "card_id", "redeemed_at"),
        Index("ix_loyalty_reward_redeemed", "redeemed_at"),
    )


class LoyaltyRewardOption(Base):
    """One entry of a programme's reward catalogue: what a ready reward can be taken as.

    Never deleted (a redeemed reward points at it); retired with `active = false`.
    """

    __tablename__ = "loyalty_reward_option"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("loyalty_program.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    #: Same shape as `LoyaltyProgram.eligibility`; NULL = drinks.
    eligibility: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: NULL = no cap beyond the programme's own `reward_max_price_pence`.
    max_price_pence: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "max_price_pence IS NULL OR max_price_pence >= 0", name="option_cap_non_negative"
        ),
        Index("ix_loyalty_reward_option_program", "program_id", "sort_order"),
    )


class LoyaltyPosReceipt(Base):
    """A Lightspeed receipt that named a customer. No names, no contacts in clear.

    `email_hash` / `phone_hash` are HMACs (the loyalty key) of the normalised contact, so
    a member who joins later can still be matched to the customer, and a non-member's
    email is never stored. `customer_label` is the till's first name plus initial, for
    staff choosing which customer to link ("Olena K.").
    """

    __tablename__ = "loyalty_pos_receipt"

    lightspeed_receipt_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    lightspeed_customer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    email_hash: Mapped[str | None] = mapped_column(String(64))
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    customer_label: Mapped[str | None] = mapped_column(String(60))
    #: Whole-receipt total as the till reported it, pence (signed; a refund is negative).
    total_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_loyalty_pos_receipt_customer", "lightspeed_customer_id", "closed_at"),
        Index("ix_loyalty_pos_receipt_closed", "closed_at"),
    )


class LoyaltyPosAward(Base):
    """What one receipt earned on one card. Reconciled on every sync; never deleted.

    One row per (card, receipt). `units` is what the receipt should be worth NOW (voids
    and refunds netted); `stamp_event_id` the live PURCHASE event carrying it. When a
    re-sync changes the answer the old event is reversed with an UNDO row and a new one
    written, so the ledger shows both.
    """

    __tablename__ = "loyalty_pos_award"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("loyalty_card.id"), nullable=False)
    lightspeed_receipt_id: Mapped[str] = mapped_column(String(80), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[PosAwardStatus] = mapped_column(enum_col(PosAwardStatus), nullable=False)
    stamp_event_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_stamp_event.id"))
    detail: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("card_id", "lightspeed_receipt_id", name="uq_loyalty_pos_award_once"),
        CheckConstraint("units >= 0", name="units_non_negative"),
        Index("ix_loyalty_pos_award_receipt", "lightspeed_receipt_id"),
    )


class LoyaltyOtp(Base):
    """A one-time recovery code. Only an HMAC of the code is stored; 10 minutes; 5 tries."""

    __tablename__ = "loyalty_otp"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("loyalty_member.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[OtpChannel] = mapped_column(enum_col(OtpChannel), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_loyalty_otp_member", "member_id", "created_at"),)


class LoyaltyCampaign(Base):
    __tablename__ = "loyalty_campaign"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The lock-screen text. 200 characters: Google truncates a message body past that.
    message: Mapped[str] = mapped_column(String(200), nullable=False)
    segment: Mapped[CampaignSegment] = mapped_column(enum_col(CampaignSegment), nullable=False)
    #: Counts towards the PECR "at most 2 promos a month" limit when true.
    is_promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    recipients: Mapped[int | None] = mapped_column(Integer)
    #: Cancelled before it went out; the scheduled send skips it.
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class LoyaltyCampaignDelivery(Base):
    """One campaign reaching one member. `returned_at`: a stamp within 7 days."""

    __tablename__ = "loyalty_campaign_delivery"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("loyalty_campaign.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("loyalty_member.id"), nullable=False)
    #: Denormalised from the campaign so the monthly promo count is one indexed query.
    is_promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    returned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint("campaign_id", "member_id", name="uq_loyalty_delivery_once"),
        Index("ix_loyalty_delivery_member", "member_id", "sent_at"),
    )


class WalletPushOutbox(Base):
    """A card changed; its wallet passes need refreshing. Drained by the wallet module.

    Written in the SAME transaction as the change, so a crash between "stamp recorded" and
    "push sent" leaves a row to retry rather than a pass that silently never updates. The
    till never waits on it (SPEC: "failure never blocks the till").
    """

    __tablename__ = "wallet_push_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("loyalty_card.id"), nullable=False)
    #: Lock-screen text; NULL is a silent refresh.
    message: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_wallet_push_outbox_due", "done_at", "next_attempt_at"),)


class LoyaltyAudit(Base):
    """Things somebody may later ask "when, and who?" about. No codes, no tokens.

    `is_alert` rows are the fraud-pattern alerts `/api/members/alerts` lists and the
    notifier sends (`notified_at` set once sent, so the scheduler can retry a send that
    failed without sending twice).
    """

    __tablename__ = "loyalty_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: e.g. "stamp_rate", "cooldown_override", "consent", "recovery_link", "erased".
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str] = mapped_column(String(400), nullable=False)
    is_alert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    staff_user_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("staff_device.id"))
    card_id: Mapped[str | None] = mapped_column(ForeignKey("loyalty_card.id"))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("loyalty_member.id"))

    __table_args__ = (
        Index("ix_loyalty_audit_alert", "is_alert", "at"),
        Index("ix_loyalty_audit_kind", "kind", "at"),
    )


__all__ = [
    "CampaignSegment",
    "LoyaltyAudit",
    "LoyaltyCampaign",
    "LoyaltyCampaignDelivery",
    "LoyaltyCard",
    "LoyaltyMember",
    "LoyaltyOtp",
    "LoyaltyPosAward",
    "LoyaltyPosReceipt",
    "LoyaltyProgram",
    "LoyaltyReward",
    "LoyaltyRewardOption",
    "LoyaltyStampEvent",
    "OtpChannel",
    "PosAwardStatus",
    "ProgramKind",
    "RewardKind",
    "StampReason",
    "WalletPushOutbox",
]
