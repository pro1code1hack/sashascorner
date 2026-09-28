"""Runtime settings (env prefix SITE_) and the café facts file.

Deliberately does not import cafeops: the site is a separate project that only
shares the SQLite file. Telegram credentials are the ops bot's own
(CAFEOPS_TELEGRAM_*), read from the environment or the repo-root .env.
"""

from __future__ import annotations

import logging
import tomllib
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SITE_ROOT = BACKEND_ROOT.parent
REPO_ROOT = SITE_ROOT.parent
CONFIG_DIR = BACKEND_ROOT / "config"

log = logging.getLogger("sashasite")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env first (shared with cafeops), then a site-local one.
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="SITE_",
    )

    database_url: str = f"sqlite+pysqlite:///{REPO_ROOT / 'cafeops.db'}"
    #: Bootstrap only: signs the owner in until a password is set in the DB
    #: (site_admin_credential), which then takes precedence. With neither, the
    #: admin login answers 503: fail closed.
    admin_password: str | None = None
    #: Shared secret the ops back office (cafeops) sends as ``X-Site-Service-Key``
    #: when it forwards an admin call for someone already signed in there. The
    #: website admin lives inside the back office now, behind its one password.
    #: Unset: only a cookie session gets into /api/admin/*.
    service_key: str | None = None
    #: Mark the admin cookie Secure even when the request looks like plain http
    #: (e.g. TLS terminated by a proxy without SITE_TRUST_PROXY).
    cookie_secure: bool = False
    admin_session_days: int = 14
    login_rate_limit_count: int = 5
    login_rate_limit_window_seconds: int = 60
    #: Comma-separated list.
    cors_origins: str = "http://localhost:4321"
    public_url: str = "http://localhost:4321"
    #: Honour the first X-Forwarded-For hop for rate limiting. Only set this when
    #: a reverse proxy you control overwrites that header.
    trust_proxy: bool = False
    rate_limit_count: int = 6
    rate_limit_window_seconds: int = 600
    local_timezone: str = "Europe/London"

    #: Where the WebP variants live. Relative paths resolve against the current
    #: directory; the result is always absolute.
    media_dir: Path = SITE_ROOT / "media"
    #: Prefix for `src`/`srcset`. The default goes through the dev proxy (/api);
    #: in production set it to /media and let Caddy serve the files.
    media_base_url: str = "/api/media-files"
    #: Photo uploads are an admin action, so a far looser brake than the forms'.
    upload_rate_limit_count: int = 60
    upload_rate_limit_window_seconds: int = 600

    @field_validator("media_dir")
    @classmethod
    def _absolute_media_dir(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @field_validator("media_base_url")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    telegram_bot_token: str | None = Field(
        default=None, validation_alias=AliasChoices("CAFEOPS_TELEGRAM_BOT_TOKEN")
    )
    telegram_owner_chat_id: int | None = Field(
        default=None, validation_alias=AliasChoices("CAFEOPS_TELEGRAM_OWNER_CHAT_ID")
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_owner_chat_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# --- café facts (config/cafe.toml) -------------------------------------------


class Address(BaseModel):
    line1: str
    city: str
    postcode: str
    country: str = Field(min_length=2, max_length=2)


class Geo(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class OpenDay(BaseModel):
    weekday: int = Field(ge=0, le=6)
    open: time | None = None
    close: time | None = None
    closed: bool = False

    @field_validator("open", "close", mode="before")
    @classmethod
    def _parse_hhmm(cls, v: object) -> object:
        if isinstance(v, str):
            return time.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _check(self) -> OpenDay:
        if self.closed:
            return self
        if self.open is None or self.close is None or self.open >= self.close:
            raise ValueError(f"weekday {self.weekday}: need open < close, or closed = true")
        return self


class Socials(BaseModel):
    instagram: str = ""
    facebook: str = ""
    tiktok: str = ""


class BookingRules(BaseModel):
    covers_per_slot: int = Field(gt=0)
    max_party: int = Field(gt=0)
    slot_minutes: int = Field(gt=0, le=240)
    duration_minutes: int = Field(gt=0)
    last_seating_before_close_minutes: int = Field(ge=0)
    min_lead_minutes: int = Field(ge=0)
    horizon_days: int = Field(gt=0)
    closed_dates: list[date] = Field(default_factory=list)


class Closure(BaseModel):
    """A date the café is shut (holiday, private event); ``note`` is shown to
    guests as the availability ``reason``."""

    date: date
    note: str = Field(default="", max_length=200)


UnconfirmedField = Literal["address", "geo", "hours", "phone", "socials", "booking", "email"]


class CafeFacts(BaseModel):
    confirmed: bool
    unconfirmed_fields: list[UnconfirmedField] = Field(default_factory=list)
    name: str
    phone: str = ""
    email: str
    address: Address
    geo: Geo
    hours: list[OpenDay]
    socials: Socials
    booking: BookingRules
    #: Filled from the DB settings; cafe.toml uses ``booking.closed_dates``.
    closures: list[Closure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_entry_per_weekday(self) -> CafeFacts:
        days = sorted(h.weekday for h in self.hours)
        if days != list(range(7)):
            raise ValueError("hours must have exactly one entry for each weekday 0..6")
        if self.booking.max_party > self.booking.covers_per_slot:
            raise ValueError("booking.max_party cannot exceed covers_per_slot")
        return self

    def day(self, weekday: int) -> OpenDay:
        return next(h for h in self.hours if h.weekday == weekday)

    def closure(self, day: date) -> Closure | None:
        found = next((c for c in self.closures if c.date == day), None)
        if found is None and day in self.booking.closed_dates:
            found = Closure(date=day)
        return found


@lru_cache(maxsize=1)
def get_cafe_file(path: Path | None = None) -> CafeFacts:
    """config/cafe.toml as written. At runtime the café settings live in the DB
    (``sashasite.settings_store.live_cafe``); this file seeds them the first time
    and keeps supplying ``confirmed`` / ``unconfirmed_fields`` metadata."""
    p = path or CONFIG_DIR / "cafe.toml"
    with p.open("rb") as fh:
        return CafeFacts.model_validate(tomllib.load(fh))


def warn_unconfirmed(cafe: CafeFacts) -> None:
    """One WARNING at startup naming every placeholder still being served."""
    if not cafe.confirmed:
        fields = ", ".join(cafe.unconfirmed_fields) or "(unspecified)"
        log.warning(
            "cafe.toml is UNCONFIRMED; still-unverified fields: %s. "
            "Check them with the owner, then set confirmed = true.",
            fields,
        )
