"""Request and response models for the back office's Members screen (CONTRACT §6)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from cafeops.api.schemas import In, Out

SegmentName = Literal["all", "lapsed_30", "reward_ready", "opted_in", "new_30", "no_wallet"]
SortName = Literal["recent", "joined", "stamps", "name"]
StickerKey = Literal["cat", "seal", "matcha", "boba", "cake", "knight", "latte", "star"]
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
    "BIRTHDAY",
    "birthday",
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
    #: Last purchase stamp or free drink; null = none since joining (BACKOFFICE-V2).
    last_visit_at: datetime | None = None


class MembersPageOut(Out):
    total: int
    members: list[MemberRowOut]
    #: Every segment's size, ignoring the search text.
    counts: dict[str, int] = Field(default_factory=dict)


class MemberDetailRowOut(MemberRowOut):
    birthday: str | None = Field(description='"DD-MM" or null')
    opt_in_at: datetime | None
    opt_in_source: str | None
    referred_by: int | None
    # --- loyalty card v2 (additive) -----------------------------------------------
    terms_accepted_at: datetime | None = None
    notes: str | None = None
    visits_per_month: float | None = None


class CardFaceOut(Out):
    card_id: str
    short_id: str
    stamps_current: int
    stamps_required: int
    stickers: list[str]
    reward_available: bool
    reward_id: int | None
    reward_text: str
    voided: bool
    can_undo: bool
    undo_label: str | None


class HistoryEntryOut(Out):
    kind: str
    title: str
    detail: str | None
    sticker: str | None
    at: datetime


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
    # --- loyalty card v2 ------------------------------------------------------------
    card: CardFaceOut
    history: list[HistoryEntryOut]


class MemberCreateIn(In):
    first_name: str = Field(min_length=1, max_length=60)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    #: "DD-MM"
    birthday: str | None = Field(default=None, max_length=5)
    marketing_opt_in: bool = False
    source: str | None = Field(default=None, max_length=40)


class MemberPatchIn(In):
    """Omitted fields are left alone; a sent null clears email, phone, birthday, notes."""

    first_name: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    birthday: str | None = Field(default=None, max_length=5)
    marketing_opt_in: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)


class StampIn(In):
    sticker: StickerKey | None = None


class GiveRewardIn(In):
    reward_id: int | None = None


class StickerIn(In):
    slot: int = Field(ge=0, le=19)
    sticker: StickerKey


class SendLinkOut(Out):
    delivery: Literal["email", "sms", "none"]
    to: str | None
    url: str
    message: str


class AlertOut(Out):
    at: datetime
    kind: str
    detail: str


class KpiOut(Out):
    value: int | float | None
    previous: int | float | None


class WeekOut(Out):
    week_start: date
    new_members: int
    stamps: int
    free_drinks: int


class SourceShareOut(Out):
    source: str | None
    label: str
    members: int
    share: float


class HourOut(Out):
    hour: int
    stamps: int


class RegularOut(Out):
    member_id: int
    first_name: str
    stamps: int


class InsightsOut(Out):
    days: int
    members: KpiOut
    joined_in_window: int
    active_members: KpiOut
    visits_per_active_member_per_month: float | None
    stamps: KpiOut
    stamps_per_week: float
    free_drinks: KpiOut
    came_back: KpiOut
    opted_in_share: float | None
    opted_in_count: int
    weekly: list[WeekOut]
    by_source: list[SourceShareOut]
    hours: list[HourOut]
    regulars: list[RegularOut]
    alerts: list[AlertOut]


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
    # --- loyalty card v2 ------------------------------------------------------------
    welcome_stamp: bool | None = None
    #: True: cards untouched for 12 months reset to zero.
    stamps_expire: bool | None = None
    #: Enabled stickers in scanner order (at least one).
    stickers: list[StickerKey] | None = Field(default=None, max_length=8)
    #: Only needed once a manager or owner exists (`pin_required`).
    manager_pin: Pin | None = Field(default=None, max_length=12)


class StickerOut(Out):
    key: str
    name: str
    url: str


class JoinSourceOut(Out):
    source: str
    members: int


class ProgramSettingsOut(ProgramAdminOut):
    """GET/PUT /api/members/program (BACKOFFICE-V2 §3). The v1 fields stay, additively."""

    welcome_stamp: bool
    stamps_expire: bool
    stickers: list[str]
    sticker_catalogue: list[StickerOut]
    join_url: str
    join_sources: list[JoinSourceOut]
    cards: int
    pin_required: bool


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
    #: Unsent campaigns only: members the segment reaches right now (opted-in only for a
    #: promo; before the monthly promo limit, which is applied per member at send time).
    audience: int | None = None
    created_by: str
    created_at: datetime
    # --- loyalty card v2 ------------------------------------------------------------
    status: Literal["draft", "scheduled", "sent", "cancelled"] = "draft"
    cancelled_at: datetime | None = None
    #: returned / recipients once the 7 days have passed or anyone returned.
    return_rate: float | None = None


class CampaignsOut(Out):
    campaigns: list[CampaignOut]
    promo_limit_per_month: int
    promos_this_month: int = 0
    audiences: dict[str, int] = Field(default_factory=dict)
    everyone_with_card: int = 0


class CampaignIn(In):
    #: Blank: the message's first words.
    title: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=1, max_length=200)
    segment: CampaignSegmentName
    scheduled_at: datetime | None = None
    is_promo: bool = True
    #: Create and send in one step.
    send_now: bool = False
    #: Operator name as typed on this device (DECISIONS.md 6). Optional.
    created_by: str | None = Field(default=None, max_length=120)


class SendOut(Out):
    recipients: int
    skipped_over_limit: int


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
    #: Cooldown: more than this many purchase stamps on one card within
    #: `cooldown_minutes` needs a manager's PIN at the scanner.
    cooldown_max_stamps: int = 3
    cooldown_minutes: int = 10


class ProgramsAdminOut(Out):
    programs: list[ProgramFullOut]
    auto_stamp: bool
    #: False while no active manager or owner has a PIN: changes then save behind the
    #: back-office password alone (otherwise nobody could ever save the first rules).
    pin_required: bool = True


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
    cooldown_max_stamps: int | None = None
    cooldown_minutes: int | None = None
    #: Required once a manager or owner exists (see `ProgramsAdminOut.pin_required`).
    manager_pin: Pin | None = Field(default=None, max_length=12)


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


# --- 90-day targets --------------------------------------------------------------


class TargetOut(Out):
    metric: str
    label: str
    unit: str = Field(description='"count" | "share" (0..1) | "rate"')
    target: float | None = Field(description="Shares are 0..1. null = no target set.")
    actual: float | None = Field(description="Over the last `days`; null = cannot be worked out.")
    progress: float | None = Field(description="actual / target; null when either is missing.")
    met: bool | None


class TargetsOut(Out):
    program_slug: str
    days: int
    targets: list[TargetOut]


class TargetsIn(In):
    """`{metric: number | null}`; null removes that target. Metrics not named stay."""

    program: str | None = Field(default=None, max_length=40)
    targets: dict[str, float | None] = Field(max_length=20)
