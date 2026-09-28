"""Request and response models for the back office's Members screen (CONTRACT §6)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from cafeops.api.schemas import In, Out

SegmentName = Literal["all", "lapsed_30", "reward_ready", "opted_in", "new_30"]
SortName = Literal["recent", "stamps", "name"]
RoleName = Literal["staff", "manager", "owner", "STAFF", "MANAGER", "OWNER"]
CampaignSegmentName = Literal[
    "ALL_OPTED_IN",
    "LAPSED_30",
    "REWARD_READY",
    "NEW_30",
    "all_opted_in",
    "lapsed_30",
    "reward_ready",
    "new_30",
]
Pin = str


class MemberRowOut(Out):
    member_id: int
    card_id: str
    first_name: str
    email: str | None
    phone: str | None
    source: str | None
    created_at: datetime
    last_activity_at: datetime
    stamps_current: int
    stamps_required: int
    cycles_completed: int
    rewards_redeemed: int
    reward_available: bool
    marketing_opt_in: bool
    wallet: str | None = Field(description='"apple" | "google" | "web" | null')


class MembersPageOut(Out):
    total: int
    members: list[MemberRowOut]


class MemberDetailRowOut(MemberRowOut):
    birthday: str | None = Field(description='"DD-MM" or null')
    opt_in_at: datetime | None
    opt_in_source: str | None
    referred_by: int | None


class EventOut(Out):
    id: int
    delta: int
    reason: str
    note: str | None
    staff_name: str | None
    device_name: str | None
    created_at: datetime
    program_slug: str = "stamp"


class RewardRowOut(Out):
    id: int
    kind: str
    issued_at: datetime
    expires_at: datetime | None
    redeemed_at: datetime | None
    redeemed_item: str | None
    staff_name: str | None
    voided_at: datetime | None
    program_slug: str = "stamp"
    redeemed_option: str | None = None


class CardRowOut(Out):
    card_id: str
    program_slug: str
    program_name: str
    program_kind: str
    stamps_current: int
    stamps_required: int
    cycles_completed: int
    reward_available: bool
    voided: bool
    created_at: datetime


class PosReceiptOut(Out):
    receipt_id: str
    closed_at: datetime
    total_pence: int
    awards: list[str]


class LightspeedLinkOut(Out):
    customer_id: str
    linked_at: datetime | None
    source: str | None = Field(description='"auto_email" | "auto_phone" | "staff" | "back_office"')
    receipts: list[PosReceiptOut]


class MemberDetailOut(Out):
    member: MemberDetailRowOut
    events: list[EventOut]
    rewards: list[RewardRowOut]
    #: Phase 3: every card the member holds (one per programme).
    cards: list[CardRowOut] = Field(default_factory=list)
    #: Phase 3: the Lightspeed customer this member is, and what their receipts earned.
    lightspeed: LightspeedLinkOut | None = None
    #: Whether receipts are turned into stamps at all (CAFEOPS_LOYALTY_AUTO_STAMP).
    auto_stamp: bool = False


class AdjustIn(In):
    delta: int = Field(ge=-24, le=24)
    reason: str = Field(min_length=5, max_length=300)
    manager_pin: Pin = Field(max_length=12)
    #: Phase 3: which card. Default: the main card.
    program: str | None = Field(default=None, max_length=40)


class LinkIn(In):
    customer_id: str | None = Field(default=None, max_length=64)
    receipt_id: str | None = Field(default=None, max_length=80)


class SourceOut(Out):
    source: str
    members: int


class DayOut(Out):
    date: date
    new_members: int
    stamps: int
    redemptions: int


class StatsOut(Out):
    members_total: int
    members_new_in_window: int
    opted_in_share: float
    by_source: list[SourceOut]
    daily: list[DayOut]
    stamp_share_of_transactions: float | None
    redemptions_per_week: float
    visits_per_member_per_month: float | None
    members_with_redemption_share: float
    campaign_return_rate: float | None
    # --- phase 3 (additive) ------------------------------------------------------
    program_slug: str = "stamp"
    #: Share of till customers seen in the window who came back on another day.
    repeat_rate_members: float | None = None
    repeat_rate_non_members: float | None = None
    repeat_customers_members: int = 0
    repeat_customers_non_members: int = 0
    #: Members only, from scans (no till needed).
    repeat_rate_members_by_scans: float | None = None
    #: Receipts that name a customer / all EPOS receipts: how much trade the comparison sees.
    receipts_with_customer_share: float | None = None
    #: Why a repeat rate is null, in a sentence.
    repeat_rate_reason: str | None = None


class ProgramAdminOut(Out):
    id: int
    slug: str
    name: str
    stamps_required: int
    max_stamps_per_scan: int
    reward_text: str
    reward_max_price_pence: int | None
    birthday_reward: bool
    referral_stamps: int
    active: bool


class ProgramIn(In):
    name: str | None = Field(default=None, max_length=80)
    stamps_required: int | None = None
    max_stamps_per_scan: int | None = None
    reward_text: str | None = Field(default=None, max_length=120)
    #: null clears the cap ("any drink").
    reward_max_price_pence: int | None = None
    birthday_reward: bool | None = None
    referral_stamps: int | None = None
    active: bool | None = None
    manager_pin: Pin = Field(max_length=12)


class StaffOut(Out):
    id: int
    name: str
    role: str
    active: bool
    telegram_id: int | None


class StaffListOut(Out):
    users: list[StaffOut]


class StaffCreateIn(In):
    name: str = Field(min_length=1, max_length=80)
    role: RoleName
    pin: Pin = Field(max_length=12)
    telegram_id: int | None = None


class StaffPatchIn(In):
    name: str | None = Field(default=None, max_length=80)
    role: RoleName | None = None
    pin: Pin | None = Field(default=None, max_length=12)
    active: bool | None = None
    #: null (sent explicitly) unlinks the Telegram account.
    telegram_id: int | None = None


class DeviceAdminOut(Out):
    id: int
    name: str
    registered_at: datetime | None
    last_seen_at: datetime | None
    revoked_at: datetime | None


class DevicesOut(Out):
    devices: list[DeviceAdminOut]


class DeviceCreateIn(In):
    name: str = Field(min_length=1, max_length=80)


class DeviceCreatedOut(Out):
    device_id: int
    pairing_code: str
    expires_at: datetime


class CampaignOut(Out):
    id: int
    title: str
    message: str
    segment: str
    is_promo: bool
    scheduled_at: datetime | None
    sent_at: datetime | None
    recipients: int | None
    returned: int
    #: Unsent campaigns only: opted-in members the segment reaches right now (before the
    #: monthly promo limit, which is applied per member at send time).
    audience: int | None = None
    created_by: str
    created_at: datetime


class CampaignsOut(Out):
    campaigns: list[CampaignOut]
    promo_limit_per_month: int


class CampaignIn(In):
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=200)
    segment: CampaignSegmentName
    scheduled_at: datetime | None = None
    is_promo: bool = True
    #: Operator name as typed on this device (DECISIONS.md 6). Optional.
    created_by: str | None = Field(default=None, max_length=120)


class SendOut(Out):
    recipients: int
    skipped_over_limit: int


class AlertOut(Out):
    at: datetime
    kind: str
    detail: str


class AlertsOut(Out):
    alerts: list[AlertOut]


# --- phase 3: programmes ---------------------------------------------------------


class EligibilityIO(Out):
    """What earns / what a reward covers. Sent back as given (unknown keys ignored)."""

    scope: Literal["drinks", "all"] = "drinks"
    categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    template_ids: list[int] = Field(default_factory=list)


class EligibilityIn(In):
    scope: Literal["drinks", "all"] = "drinks"
    categories: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    template_ids: list[int] = Field(default_factory=list, max_length=50)


class RewardOptionAdminOut(Out):
    id: int
    name: str
    description: str | None
    eligibility: EligibilityIO
    covers: str
    max_price_pence: int | None
    active: bool


class RewardOptionIn(In):
    id: int | None = None
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=200)
    eligibility: EligibilityIn | None = None
    max_price_pence: int | None = Field(default=None, ge=0, le=100_000)
    active: bool = True


class ProgramFullOut(ProgramAdminOut):
    kind: str = Field(description='"STAMPS" | "POINTS"')
    points_per_pound: int | None
    eligibility: EligibilityIO
    earns: str = Field(description="The eligibility, as a sentence.")
    description: str | None
    reward_ready_label: str
    sort_order: int
    is_default: bool
    reward_options: list[RewardOptionAdminOut]
    cards: int
    #: False once anybody has earned on it: the kind can no longer change.
    kind_editable: bool


class ProgramsAdminOut(Out):
    programs: list[ProgramFullOut]
    auto_stamp: bool


class ProgramEditIn(In):
    """Phase 3 editor. Omitted fields are left alone; `null` on reward_max_price_pence
    clears the cap, `null` on eligibility resets it to "drinks"."""

    name: str | None = Field(default=None, max_length=80)
    kind: Literal["STAMPS", "POINTS"] | None = None
    stamps_required: int | None = None
    points_per_pound: int | None = None
    max_stamps_per_scan: int | None = None
    reward_text: str | None = Field(default=None, max_length=120)
    reward_max_price_pence: int | None = None
    reward_ready_label: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    birthday_reward: bool | None = None
    referral_stamps: int | None = None
    active: bool | None = None
    sort_order: int | None = None
    eligibility: EligibilityIn | None = None
    reward_options: list[RewardOptionIn] | None = Field(default=None, max_length=12)
    manager_pin: Pin = Field(max_length=12)


class ProgramCreateIn(ProgramEditIn):
    slug: str = Field(min_length=3, max_length=40)


class TemplateRefOut(Out):
    id: int
    name: str


class MenuFacetsOut(Out):
    categories: list[str]
    templates: list[TemplateRefOut]


class EligibilityPreviewOut(Out):
    count: int
    sample: list[str]
    covers: str


class PosCustomerAdminOut(Out):
    customer_id: str
    label: str | None
    last_receipt_id: str
    last_at: datetime
    receipts: int


class PosCustomersAdminOut(Out):
    customers: list[PosCustomerAdminOut]
