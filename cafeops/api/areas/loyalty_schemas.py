"""Request and response models for the public loyalty API (CONTRACT §4).

Served on the public domain through Caddy, so every request body forbids unknown fields
(`In`) and every string has a ceiling: a join form is the one endpoint anybody on the
internet can write to.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from cafeops.api.schemas import In, Out


class WalletsAvailable(Out):
    apple: bool
    google: bool


class RewardOptionOut(Out):
    """One entry of a programme's reward catalogue, as customers see it."""

    name: str
    description: str | None
    max_price_pence: int | None = Field(description="NULL: no cap beyond the programme's.")


class ProgramOut(Out):
    name: str
    stamps_required: int
    reward_text: str
    birthday_reward: bool
    referral_stamps: int
    max_stamps_per_scan: int
    wallets: WalletsAvailable
    # --- additive: what the /rewards page renders instead of its built-in defaults ----
    slug: str = "stamp"
    kind: str = Field(default="STAMPS", description='"STAMPS" | "POINTS"')
    description: str | None = None
    reward_ready_label: str = "Free drink ready"
    #: NULL: the free drink can be any drink.
    reward_max_price_pence: int | None = None
    points_per_pound: int | None = None
    #: What a full card can be taken as. Empty: just `reward_text`.
    catalogue: list[RewardOptionOut] = Field(default_factory=list)


class JoinIn(In):
    first_name: str = Field(max_length=80)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    birthday_day: int | None = Field(default=None, ge=1, le=31)
    birthday_month: int | None = Field(default=None, ge=1, le=12)
    terms: bool
    marketing_opt_in: bool = False
    src: str | None = Field(default=None, max_length=80)
    ref: str | None = Field(default=None, max_length=36, description="Referrer's card id.")
    #: Honeypot. People never see it; form-filling bots do.
    website: str = Field(default="", max_length=200)
    #: Phase 3: other active programmes to join in the same step (their slugs).
    also_join: list[str] = Field(default_factory=list, max_length=5)


class ExtraCardOut(Out):
    program_slug: str
    card_id: str
    token: str
    web_card_url: str


class JoinResult(Out):
    card_id: str
    token: str
    web_card_url: str = Field(description='"/c/<card_id>#t=<token>"')
    apple_pass_url: str | None
    google_save_url: str | None
    #: Phase 3: cards made in the same step for `also_join` programmes.
    extra_cards: list[ExtraCardOut] = Field(default_factory=list)


class PublicProgramOut(Out):
    """A programme as customers see it (phase 3)."""

    slug: str
    name: str
    kind: str = Field(description='"STAMPS" | "POINTS"')
    description: str | None
    stamps_required: int = Field(description="Stamps, or points, for a reward.")
    points_per_pound: int | None
    reward_text: str
    reward_ready_label: str
    is_default: bool
    catalogue: list[RewardOptionOut] = Field(default_factory=list)


class ProgramsOut(Out):
    programs: list[PublicProgramOut]


class JoinProgramIn(In):
    program: str = Field(max_length=40, description="The programme's slug.")


class SiblingCardOut(Out):
    """Another card of the same member (phase 3). The token is theirs already: whoever
    holds one of a member's card links holds that member."""

    card_id: str
    token: str
    program_slug: str
    program_name: str
    program_kind: str
    stamps_current: int
    stamps_required: int
    reward_ready: bool
    web_card_url: str


class RewardOut(Out):
    id: int
    kind: str = Field(description="STAMP_CARD | BIRTHDAY | REFERRAL")
    label: str
    expires_at: datetime | None


class WalletLinks(Out):
    apple_pass_url: str | None
    google_save_url: str | None


class CardState(Out):
    card_id: str
    first_name: str
    member_since: date
    program_name: str
    stamps_required: int
    stamps_current: int
    reward_text: str
    rewards: list[RewardOut]
    qr_payload: str
    marketing_opt_in: bool
    updated_at: datetime
    wallets: WalletLinks
    # --- phase 3 (additive) ------------------------------------------------------
    program_slug: str = "stamp"
    program_kind: str = Field(default="STAMPS", description='"STAMPS" | "POINTS"')
    points_per_pound: int | None = None
    reward_ready_label: str = "Free drink ready"
    program_description: str | None = None
    other_cards: list[SiblingCardOut] = Field(default_factory=list)
    joinable: list[PublicProgramOut] = Field(default_factory=list)
    # --- additive: the member's own details, editable from the web card ----------
    #: Stamps a referrer earns when a friend who joined through their link first visits.
    #: 0: the programme runs no referrals, so the web card offers no invite link.
    referral_stamps: int = 0
    #: The programme gives a birthday drink at all (else the card hides the birthday).
    birthday_reward: bool = False
    birthday_day: int | None = None
    birthday_month: int | None = None
    #: The first birthday that will bring a drink, after the 30-day rule. NULL: none set.
    birthday_counts_from: date | None = None
    #: BACKOFFICE-V2 §2: the sticker key in each filled slot (slot-N art: cat, seal,
    #: matcha, boba, cake, knight, latte, star). Empty on a points card.
    stickers: list[str] = Field(default_factory=list)
    #: "e***@example.com" / "*******123": which contact recovery uses. Never in full.
    contact_masked: str | None = None


class PreferencesIn(In):
    """Any subset of the member's own details. A field left out is left alone.

    `birthday_day` and `birthday_month` go together; both `null` removes the birthday.
    Email and phone are not editable here: they are how a lost card is recovered, and a
    typo would lock the member out. Staff change them from the back office.
    """

    marketing_opt_in: bool | None = None
    first_name: str | None = Field(default=None, max_length=80)
    birthday_day: int | None = Field(default=None, ge=1, le=31)
    birthday_month: int | None = Field(default=None, ge=1, le=12)


class RecoverIn(In):
    contact: str = Field(max_length=254)
    website: str = Field(default="", max_length=200)


class RecoverOut(Out):
    delivery: str = Field(description='"email" | "sms" | "ask_staff"')


class VerifyIn(In):
    contact: str = Field(max_length=254)
    code: str = Field(max_length=12)
