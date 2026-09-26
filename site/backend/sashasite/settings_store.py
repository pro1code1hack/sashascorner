"""Café settings in the DB (``site_setting``), edited in the admin.

Three keys, each a JSON document validated by the models below:

- ``cafe``: name, phone, email, address, geo, hours, socials;
- ``booking``: capacity and timing rules;
- ``closures``: ``[{date, note}]``.

They are seeded from ``config/cafe.toml`` the first time they are read (each key
independently, so seeding is idempotent and never overwrites an edit). After that
the file only supplies ``confirmed`` / ``unconfirmed_fields``: that list is
metadata about what the owner has verified, not a setting.

``live_cafe()`` returns the same ``CafeFacts`` shape the booking and info code
already took from the file, so they switch source without changing shape.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from sashasite.config import Address, CafeFacts, Closure, Geo, get_cafe_file, get_settings
from sashasite.db import SiteSetting, session_scope, utcnow

KEYS = ("cafe", "booking", "closures")
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoursDay(_Strict):
    weekday: int = Field(ge=0, le=6)
    open: str | None = Field(default=None, pattern=HHMM)
    close: str | None = Field(default=None, pattern=HHMM)
    closed: bool = False

    @model_validator(mode="after")
    def _check(self) -> HoursDay:
        if self.closed:
            self.open = None
            self.close = None
            return self
        if self.open is None or self.close is None:
            raise ValueError(f"weekday {self.weekday}: give open and close, or closed = true")
        if self.open >= self.close:  # zero-padded HH:MM compares correctly as text
            raise ValueError(f"weekday {self.weekday}: open must be before close")
        return self


def _url_or_empty(v: str) -> str:
    v = v.strip()
    if v and not v.startswith("https://"):
        raise ValueError("must be an https:// link, or empty")
    return v


class SocialsSettings(_Strict):
    instagram: str = Field(default="", max_length=300)
    facebook: str = Field(default="", max_length=300)
    tiktok: str = Field(default="", max_length=300)

    _urls = field_validator("instagram", "facebook", "tiktok")(_url_or_empty)


class CafeSettings(_Strict):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(default="", max_length=30)
    #: Empty until the café has a mailbox.
    email: EmailStr | None = None
    address: Address
    geo: Geo
    hours: list[HoursDay]
    socials: SocialsSettings

    @field_validator("email", mode="before")
    @classmethod
    def _blank_email(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_serializer("email")
    def _email_out(self, v: str | None) -> str:
        return v or ""  # "" like /api/info: no mailbox yet

    @field_validator("name", "phone", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _seven_days(self) -> CafeSettings:
        if sorted(h.weekday for h in self.hours) != list(range(7)):
            raise ValueError("hours must have exactly one entry for each weekday 0..6")
        self.hours.sort(key=lambda h: h.weekday)
        return self


class BookingSettings(_Strict):
    covers_per_slot: int = Field(ge=1, le=500)
    max_party: int = Field(ge=1)
    slot_minutes: int = Field(ge=5, le=240)
    duration_minutes: int = Field(ge=15, le=600)
    last_seating_before_close_minutes: int = Field(ge=0, le=600)
    min_lead_minutes: int = Field(ge=0, le=10080)
    horizon_days: int = Field(ge=1, le=365)

    @model_validator(mode="after")
    def _party_fits(self) -> BookingSettings:
        if self.max_party > self.covers_per_slot:
            raise ValueError("max_party cannot exceed covers_per_slot")
        return self


class ClosureSettings(_Strict):
    date: dt.date
    note: str = Field(default="", max_length=200)

    @field_validator("note", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class SettingsOut(BaseModel):
    cafe: CafeSettings
    booking: BookingSettings
    closures: list[ClosureSettings]
    telegram_configured: bool


class CafePatch(_Strict):
    """Any subset of the café fields; each given field replaces the stored one."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: Address | None = None
    geo: Geo | None = None
    hours: list[HoursDay] | None = None
    socials: SocialsSettings | None = None


class BookingPatch(_Strict):
    covers_per_slot: int | None = None
    max_party: int | None = None
    slot_minutes: int | None = None
    duration_minutes: int | None = None
    last_seating_before_close_minutes: int | None = None
    min_lead_minutes: int | None = None
    horizon_days: int | None = None


class SettingsPutIn(_Strict):
    """Any subset of the three sections. ``cafe`` and ``booking`` merge field by
    field over what is stored and are then validated whole; ``closures`` replaces
    the list."""

    cafe: CafePatch | None = None
    booking: BookingPatch | None = None
    closures: list[ClosureSettings] | None = None

    @model_validator(mode="after")
    def _unique_closures(self) -> SettingsPutIn:
        if self.closures is not None:
            dates = [c.date for c in self.closures]
            dupes = sorted({d.isoformat() for d in dates if dates.count(d) > 1})
            if dupes:
                raise ValueError(f"closures: each date once; repeated {', '.join(dupes)}")
            self.closures.sort(key=lambda c: c.date)
        return self


class SettingsInvalid(Exception):
    """The merged section failed validation. ``errors`` are pydantic-style, with
    ``loc`` rooted at ``("body", section)`` like FastAPI's own 422s."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("invalid settings")
        self.errors = errors


def _validate[M: BaseModel](section: str, model: type[M], data: dict[str, Any]) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errs = exc.errors(include_url=False, include_context=False)
        raise SettingsInvalid([{**e, "loc": ("body", section, *e["loc"])} for e in errs]) from exc


# --- storage --------------------------------------------------------------------


def _seed_values() -> dict[str, Any]:
    f = get_cafe_file()
    cafe = CafeSettings(
        name=f.name,
        phone=f.phone,
        email=f.email or None,
        address=f.address,
        geo=f.geo,
        hours=[
            HoursDay(
                weekday=h.weekday,
                open=h.open.strftime("%H:%M") if h.open else None,
                close=h.close.strftime("%H:%M") if h.close else None,
                closed=h.closed,
            )
            for h in f.hours
        ],
        socials=SocialsSettings(**f.socials.model_dump()),
    )
    booking = BookingSettings(**f.booking.model_dump(exclude={"closed_dates"}))
    closures = [ClosureSettings(date=d) for d in sorted(set(f.booking.closed_dates))]
    return {
        "cafe": cafe.model_dump(mode="json"),
        "booking": booking.model_dump(mode="json"),
        "closures": [c.model_dump(mode="json") for c in closures],
    }


def _raw(session: Session) -> dict[str, Any]:
    rows = session.scalars(select(SiteSetting).where(SiteSetting.key.in_(KEYS)))
    return {r.key: json.loads(r.value_json) for r in rows}


def ensure_seeded() -> list[str]:
    """Insert any missing key from cafe.toml. Returns the keys it seeded."""
    with session_scope() as session:
        if len(_raw(session)) == len(KEYS):
            return []
    with session_scope(immediate=True) as session:  # re-check under the write lock
        have = _raw(session)
        seed = _seed_values()
        missing = [k for k in KEYS if k not in have]
        for k in missing:
            session.add(SiteSetting(key=k, value_json=json.dumps(seed[k]), updated_at=utcnow()))
        return missing


def _load(session: Session) -> tuple[CafeSettings, BookingSettings, list[ClosureSettings]]:
    raw = _raw(session)
    return (
        CafeSettings.model_validate(raw["cafe"]),
        BookingSettings.model_validate(raw["booking"]),
        [ClosureSettings.model_validate(c) for c in raw["closures"]],
    )


def load_settings() -> SettingsOut:
    ensure_seeded()
    with session_scope() as session:
        cafe, booking, closures = _load(session)
    return SettingsOut(
        cafe=cafe,
        booking=booking,
        closures=closures,
        telegram_configured=get_settings().telegram_configured,
    )


def update_settings(body: SettingsPutIn) -> tuple[SettingsOut, list[str]]:
    """Merge, validate whole, store. Raises SettingsInvalid (-> 422).
    Returns the new settings and the keys that changed."""
    ensure_seeded()
    changed: list[str] = []
    with session_scope(immediate=True) as session:
        cafe, booking, _closures_now = _load(session)
        new: dict[str, Any] = {}
        if body.cafe is not None:
            merged = cafe.model_dump() | body.cafe.model_dump(exclude_unset=True)
            new["cafe"] = _validate("cafe", CafeSettings, merged).model_dump(mode="json")
        if body.booking is not None:
            patch = body.booking.model_dump(exclude_unset=True, exclude_none=True)
            merged = booking.model_dump() | patch
            new["booking"] = _validate("booking", BookingSettings, merged).model_dump(mode="json")
        if body.closures is not None:
            new["closures"] = [c.model_dump(mode="json") for c in body.closures]
        now = utcnow()
        for key, value in new.items():
            row = session.get(SiteSetting, key)
            assert row is not None  # ensure_seeded
            if json.loads(row.value_json) != value:
                row.value_json = json.dumps(value)
                row.updated_at = now
                changed.append(key)
    return load_settings(), changed


def live_cafe() -> CafeFacts:
    """The café as the site serves it: DB settings, plus the file's confirmation
    metadata. Same shape as ``get_cafe_file()``."""
    s = load_settings()
    f = get_cafe_file()
    return CafeFacts.model_validate(
        {
            "confirmed": f.confirmed,
            "unconfirmed_fields": f.unconfirmed_fields,
            "name": s.cafe.name,
            "phone": s.cafe.phone,
            "email": s.cafe.email or "",
            "address": s.cafe.address.model_dump(),
            "geo": s.cafe.geo.model_dump(),
            "hours": [h.model_dump() for h in s.cafe.hours],
            "socials": s.cafe.socials.model_dump(),
            "booking": s.booking.model_dump() | {"closed_dates": []},
            "closures": [Closure(date=c.date, note=c.note) for c in s.closures],
        }
    )
