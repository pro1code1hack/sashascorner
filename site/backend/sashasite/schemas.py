"""API request/response models. The Astro frontend consumes these shapes."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

# --- menu -------------------------------------------------------------------------


class SizeOut(BaseModel):
    code: str
    label: str
    price_pence: int


class SeasonalOut(BaseModel):
    name: str


class MenuItemOut(BaseModel):
    id: str
    name: str
    description: str | None
    note: str | None
    signature: bool
    seasonal: SeasonalOut | None
    sizes: list[SizeOut]
    #: Always null for now. TODO: once the ops back office confirms its media URL
    #: scheme (``/media/<sha256>.<ext>``), map ``menu_item.photo_asset_id`` to it.
    photo: str | None = None


class MenuCategoryOut(BaseModel):
    slug: str
    name: str
    blurb: str
    items: list[MenuItemOut]


class ExtraOut(BaseModel):
    name: str
    price_text: str


class ExtrasOut(BaseModel):
    title: str
    items: list[ExtraOut]


class MenuOut(BaseModel):
    generated_at: dt.datetime
    #: ``ops`` = read from the back office's menu; ``board`` = config/menu_board.toml.
    source: Literal["ops", "board"] = "board"
    #: Short hash of everything below except ``generated_at``: re-render when it changes.
    version: str = ""
    categories: list[MenuCategoryOut]
    extras: ExtrasOut


# --- info -------------------------------------------------------------------------


class AddressOut(BaseModel):
    line1: str
    city: str
    postcode: str
    country: str


class GeoOut(BaseModel):
    lat: float
    lng: float


class HoursOpenOut(BaseModel):
    weekday: int
    open: str
    close: str


class HoursClosedOut(BaseModel):
    weekday: int
    closed: Literal[True] = True


class SocialsOut(BaseModel):
    instagram: str
    facebook: str
    tiktok: str


class BookingInfoOut(BaseModel):
    max_party: int
    slot_minutes: int
    horizon_days: int
    min_lead_minutes: int


class ClosureOut(BaseModel):
    date: dt.date
    note: str = ""


class InfoOut(BaseModel):
    name: str
    address: AddressOut
    geo: GeoOut
    phone: str
    email: str
    hours: list[HoursOpenOut | HoursClosedOut]
    socials: SocialsOut
    booking: BookingInfoOut
    confirmed: bool
    #: Upcoming dates the café is shut (today onwards), so "open now" and the hours
    #: tables can say so at runtime without a rebuild.
    closures: list[ClosureOut] = []


# --- availability / bookings ------------------------------------------------------------


class SlotOut(BaseModel):
    time: str
    available: bool
    remaining_covers: int


class AvailabilityOut(BaseModel):
    date: dt.date
    party: int
    closed: bool
    reason: str | None
    slots: list[SlotOut]


class BookingIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=30)
    party: int = Field(ge=1)
    date: dt.date
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notes: str | None = Field(default=None, max_length=500)
    website: str = ""  # honeypot: humans never see it, so it must stay empty

    @field_validator("name", "phone", "notes", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return v
        return v

    @field_validator("phone", "notes")
    @classmethod
    def _empty_is_none(cls, v: str | None) -> str | None:
        return v or None


class BookingCreatedOut(BaseModel):
    reference: str
    manage_token: str
    status: Literal["confirmed", "cancelled"]
    date: dt.date
    time: str
    party: int
    name: str


class BookingOut(BookingCreatedOut):
    created_at: dt.datetime


# --- admin: bookings ----------------------------------------------------------------

BookingStatus = Literal["confirmed", "cancelled", "arrived", "no_show"]
BookingSource = Literal["web", "admin"]
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class AdminBookingOut(BaseModel):
    id: int
    reference: str
    name: str
    #: Null for a phone-in booking taken without an email.
    email: str | None
    phone: str | None
    party: int
    date: dt.date
    time: str
    status: BookingStatus
    notes: str | None
    source: BookingSource
    created_at: dt.datetime
    cancelled_at: dt.datetime | None


def _strip_or_none(v: object) -> object:
    if isinstance(v, str):
        return v.strip() or None
    return v


class AdminBookingIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    party: int = Field(ge=1, le=500)
    date: dt.date
    time: str = Field(pattern=HHMM)
    notes: str | None = Field(default=None, max_length=500)
    #: Book even if it takes the slot past covers_per_slot.
    override_capacity: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    _blank = field_validator("phone", "email", "notes", mode="before")(_strip_or_none)


class AdminBookingPatchIn(BaseModel):
    status: BookingStatus | None = None
    notes: str | None = Field(default=None, max_length=500)
    party: int | None = Field(default=None, ge=1, le=500)
    date: dt.date | None = None
    time: str | None = Field(default=None, pattern=HHMM)
    #: Not in the contract's list, but the same escape hatch as creation.
    override_capacity: bool = False

    _blank = field_validator("notes", mode="before")(_strip_or_none)


class SlotCoversOut(BaseModel):
    time: str
    #: Covers seated at that moment (bookings whose hold spans it).
    covers_booked: int
    capacity: int


class DayOut(BaseModel):
    date: dt.date
    closed: bool
    #: The closure note, or why the day is closed; null when open.
    reason: str | None
    capacity: int
    slots: list[SlotCoversOut]
    bookings: list[AdminBookingOut]


# --- admin: auth, messages, summary ---------------------------------------------------


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    authenticated: Literal[True] = True
    password_source: Literal["db", "env"]


class PasswordChangeIn(BaseModel):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=10, max_length=256)


MessageStatus = Literal["new", "handled", "archived"]


class MessageOut(BaseModel):
    id: int
    name: str
    email: str
    topic: str
    message: str
    created_at: dt.datetime
    status: MessageStatus
    handled_at: dt.datetime | None


class MessagePatchIn(BaseModel):
    status: MessageStatus


class TodayOut(BaseModel):
    date: dt.date
    closed: bool
    #: Bookings holding covers today (everything but cancellations).
    bookings: int
    covers: int
    #: covers_per_slot: how many can be seated at once.
    capacity: int
    #: Confirmed bookings still to start today, earliest first (up to 5).
    next: list[AdminBookingOut]


class WeekDayOut(BaseModel):
    date: dt.date
    bookings: int
    covers: int


class MenuSummaryOut(BaseModel):
    source: Literal["ops", "board"]
    warnings: int


class SummaryOut(BaseModel):
    today: TodayOut
    week: list[WeekDayOut]
    messages_new: int
    #: Image slots with no photo yet.
    photos_missing: int
    menu: MenuSummaryOut


# --- contact -------------------------------------------------------------------------


Topic = Literal["general", "order", "events", "feedback", "press", "jobs"]


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    topic: Topic
    message: str = Field(min_length=10, max_length=2000)
    website: str = ""

    @field_validator("name", "message", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class OkOut(BaseModel):
    ok: bool = True


# --- media & image slots -------------------------------------------------------------


class Focal(BaseModel):
    """Focal point as fractions of the image: (0, 0) top-left, (1, 1) bottom-right."""

    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class SlotItemOut(BaseModel):
    media_id: int
    #: The 960px variant (or the largest there is, for a smaller original).
    src: str
    #: Every variant, "url 480w, url 960w, url 1600w".
    srcset: str
    width: int
    height: int
    #: alt_override ?? media.alt
    alt: str
    focal: Focal
    #: data:image/webp;base64,... -- a tiny blurred preview.
    blur: str


class ImageSlotOut(BaseModel):
    label: str
    page: str
    aspect: str
    multiple: bool
    max: int
    hint: str
    items: list[SlotItemOut]


class ImageSlotsOut(BaseModel):
    slots: dict[str, ImageSlotOut]


class SlotItemIn(BaseModel):
    media_id: int
    #: Per-slot alt text; omitted, null or blank means "use the photo's own".
    alt: str | None = Field(default=None, max_length=300)
    focal: Focal = Field(default_factory=Focal)


class SlotPutIn(BaseModel):
    items: list[SlotItemIn]


class MediaUsageOut(BaseModel):
    slot_key: str
    position: int
    #: The slot's label, or null when the key is no longer in config/slots.toml.
    label: str | None


class MediaOut(BaseModel):
    id: int
    sha256: str
    original_name: str
    content_type: str
    width: int
    height: int
    bytes: int
    alt: str
    created_at: dt.datetime
    widths: list[int]
    src: str
    srcset: str
    blur: str
    usage: list[MediaUsageOut]


class MediaPatchIn(BaseModel):
    alt: str = Field(max_length=300)

    @field_validator("alt", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class MediaDeletedOut(BaseModel):
    ok: bool = True
    id: int
    #: Slots the photo was removed from (only non-empty with ?force=1).
    unassigned: list[str]
