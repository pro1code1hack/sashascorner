"""Request and response models for the staff scanner API (CONTRACT §5)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from cafeops.api.areas.loyalty_schemas import RewardOut
from cafeops.api.schemas import In, Out


class PairIn(In):
    pairing_code: str = Field(max_length=12)
    device_name: str = Field(default="", max_length=80)


class PairOut(Out):
    device_token: str


class LoginIn(In):
    pin: str = Field(max_length=12)


class StaffUserOut(Out):
    id: int
    name: str
    role: str = Field(description='"staff" | "manager" | "owner"')


class LoginOut(Out):
    session_token: str
    expires_at: datetime
    user: StaffUserOut


class DeviceOut(Out):
    id: int
    name: str


class MeOut(Out):
    user: StaffUserOut
    device: DeviceOut
    expires_at: datetime


class ScanIn(In):
    payload: str = Field(max_length=200)


class LastEventOut(Out):
    id: int
    delta: int
    reason: str
    at: datetime
    staff_name: str | None


class RewardOptionOut(Out):
    """A reward-catalogue entry (phase 3): what a ready reward can be taken as."""

    id: int
    name: str
    description: str | None
    max_price_pence: int | None
    covers: str


class ProgramRefOut(Out):
    slug: str
    name: str
    kind: str


class ScanResult(Out):
    card_id: str
    first_name: str
    member_since: date
    stamps_current: int
    stamps_required: int
    rewards: list[RewardOut]
    stamps_last_10_min: int
    last_event: LastEventOut | None
    voided: bool
    # --- phase 3 (additive) ------------------------------------------------------
    program_slug: str = "stamp"
    program_name: str = ""
    program_kind: str = Field(default="STAMPS", description='"STAMPS" | "POINTS"')
    points_per_pound: int | None = None
    reward_ready_label: str = "Free drink ready"
    max_stamps_per_scan: int = 3
    reward_options: list[RewardOptionOut] = Field(default_factory=list)
    #: The member's other live cards, so any one scan shows all of them.
    other_cards: list[ScanResult] = Field(default_factory=list)
    joinable: list[ProgramRefOut] = Field(default_factory=list)
    lightspeed_linked: bool = False


class StampIn(In):
    card_id: str = Field(max_length=36)
    delta: int = Field(default=1, ge=1, le=10)
    manager_pin: str | None = Field(default=None, max_length=12)


class StampOut(Out):
    card: ScanResult
    event_id: int
    undo_until: datetime
    reward_issued: bool


class RedeemIn(In):
    reward_id: int
    menu_item_id: int | None = None
    #: Phase 3: which catalogue entry. Optional: a single entry is implied, and with
    #: several the first that covers `menu_item_id` is taken.
    option_id: int | None = None


class SpendIn(In):
    """Phase 3: points for what the customer paid (POINTS cards)."""

    card_id: str = Field(max_length=36)
    spend_pence: int = Field(ge=1, le=100_000)
    manager_pin: str | None = Field(default=None, max_length=12)


class AddCardIn(In):
    card_id: str = Field(max_length=36, description="Any of the member's cards.")
    program: str = Field(max_length=40)


class LinkPosIn(In):
    card_id: str = Field(max_length=36)
    customer_id: str | None = Field(default=None, max_length=64)
    receipt_id: str | None = Field(default=None, max_length=80)


class PosCustomerOut(Out):
    customer_id: str
    label: str | None
    last_receipt_id: str
    last_at: datetime
    receipts: int


class PosCustomersOut(Out):
    customers: list[PosCustomerOut]


class RedeemOut(Out):
    card: ScanResult
    reward_id: int
    undo_until: datetime


class MigrateIn(In):
    card_id: str = Field(max_length=36)
    paper_stamps: int = Field(ge=1, le=7)


class UndoIn(In):
    event_id: int | None = None
    reward_id: int | None = None


class CardOut(Out):
    card: ScanResult


class DrinkOut(Out):
    menu_item_id: int
    name: str
    category: str | None
    price_pence: int | None


class DrinksOut(Out):
    items: list[DrinkOut]
    #: Phase 3, with `option_id`/`reward_id`: the cap that applies, and what is covered.
    max_price_pence: int | None = None
    covers: str | None = None


class LookupMemberOut(Out):
    card_id: str
    first_name: str
    contact_masked: str


class LookupOut(Out):
    members: list[LookupMemberOut]


class RecoveryLinkIn(In):
    card_id: str = Field(max_length=36)


class RecoveryLinkOut(Out):
    url: str
