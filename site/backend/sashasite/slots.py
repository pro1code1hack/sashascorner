"""Image slots: the registry (config/slots.toml) and what is assigned to each slot.

The registry is owned by whoever lays out the pages; the assignments live in
``site_slot_item``. An assignment whose key has left the registry is KEPT (the
owner's choice of photo is not thrown away by a layout edit) but omitted from
``/api/slots``; ``sashasite doctor`` lists it.
"""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sashasite.config import CONFIG_DIR
from sashasite.db import SiteMedia, SiteSlotItem, session_scope, utcnow
from sashasite.media import media_urls
from sashasite.schemas import Focal, ImageSlotOut, ImageSlotsOut, SlotItemIn, SlotItemOut

SLOTS_PATH = CONFIG_DIR / "slots.toml"

#: page.section[.name]: 2-4 lowercase dot-separated segments.
KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*){1,3}$"


class SlotDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=KEY_PATTERN, max_length=80)
    label: str = Field(min_length=1)
    page: str = Field(pattern=r"^/")
    aspect: str = Field(pattern=r"^[1-9]\d*/[1-9]\d*$")
    multiple: bool = False
    max: int = Field(default=1, ge=1)
    hint: str = ""

    @model_validator(mode="after")
    def _single_means_one(self) -> SlotDef:
        if not self.multiple and self.max != 1:
            raise ValueError(f"slot {self.key!r}: multiple = false needs max = 1, got {self.max}")
        return self


class SlotRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: list[SlotDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> SlotRegistry:
        keys = [s.key for s in self.slot]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"duplicate slot key(s): {dupes}")
        return self

    def get(self, key: str) -> SlotDef | None:
        return next((s for s in self.slot if s.key == key), None)

    @property
    def keys(self) -> set[str]:
        return {s.key for s in self.slot}


def load_registry(path: Path = SLOTS_PATH) -> SlotRegistry:
    """Parse and validate. A missing file is an empty registry (no slots)."""
    if not path.exists():
        return SlotRegistry()
    with path.open("rb") as fh:
        return SlotRegistry.model_validate(tomllib.load(fh))


_lock = threading.Lock()
_cache: tuple[int, SlotRegistry] | None = None  # (file mtime_ns or -1, registry)


def registry() -> SlotRegistry:
    """Reloaded whenever the file's mtime changes. A broken edit raises (500 +
    log) rather than silently serving the previous registry."""
    global _cache
    with _lock:
        mtime = SLOTS_PATH.stat().st_mtime_ns if SLOTS_PATH.exists() else -1
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]
        reg = load_registry()
        _cache = (mtime, reg)
        return reg


# --- output --------------------------------------------------------------------


def item_out(it: SiteSlotItem) -> SlotItemOut:
    m = it.media
    src, srcset = media_urls(m)
    return SlotItemOut(
        media_id=m.id,
        src=src,
        srcset=srcset,
        width=m.width,
        height=m.height,
        alt=it.alt_override if it.alt_override is not None else m.alt,
        focal=Focal(x=it.focal_x, y=it.focal_y),
        blur=m.blur,
    )


def slot_out(d: SlotDef, items: list[SiteSlotItem]) -> ImageSlotOut:
    return ImageSlotOut(
        label=d.label,
        page=d.page,
        aspect=d.aspect,
        multiple=d.multiple,
        max=d.max,
        hint=d.hint,
        # If the layout's max was lowered after assignment, show what fits; the
        # doctor reports the overflow.
        items=[item_out(it) for it in items[: d.max]],
    )


def _items_by_key(session: Session) -> dict[str, list[SiteSlotItem]]:
    rows = session.scalars(
        select(SiteSlotItem).order_by(SiteSlotItem.slot_key, SiteSlotItem.position)
    ).all()
    out: dict[str, list[SiteSlotItem]] = {}
    for r in rows:
        out.setdefault(r.slot_key, []).append(r)
    return out


def build_slots(reg: SlotRegistry | None = None) -> ImageSlotsOut:
    reg = reg or registry()
    with session_scope() as session:
        by_key = _items_by_key(session)
        return ImageSlotsOut(slots={d.key: slot_out(d, by_key.get(d.key, [])) for d in reg.slot})


# --- writes --------------------------------------------------------------------


class SlotError(ValueError):
    """The request is well-formed but cannot be applied (maps to 422)."""


def replace_slot(key: str, items: list[SlotItemIn]) -> ImageSlotOut:
    """Replace the whole ordered list for ``key`` in one IMMEDIATE transaction:
    either every item lands at positions 0..n-1 or nothing changes."""
    d = registry().get(key)
    if d is None:
        raise SlotError(f"Unknown slot {key!r}: it is not declared in config/slots.toml.")
    if len(items) > d.max:
        raise SlotError(f"Slot {key!r} shows at most {d.max} photo(s); got {len(items)}.")
    with session_scope(immediate=True) as session:
        ids = {i.media_id for i in items}
        found = set(session.scalars(select(SiteMedia.id).where(SiteMedia.id.in_(ids))).all())
        missing = sorted(ids - found)
        if missing:
            raise SlotError(f"No such photo(s): {missing}.")
        session.execute(delete(SiteSlotItem).where(SiteSlotItem.slot_key == key))
        session.flush()
        now = utcnow()
        new = [
            SiteSlotItem(
                slot_key=key,
                position=pos,
                media_id=i.media_id,
                alt_override=(i.alt or "").strip() or None,
                focal_x=i.focal.x,
                focal_y=i.focal.y,
                updated_at=now,
            )
            for pos, i in enumerate(items)
        ]
        session.add_all(new)
        session.flush()
        for it in new:
            session.refresh(it)
        return slot_out(d, new)


def current_items(key: str) -> list[SlotItemIn]:
    """The slot's list as PUT input, so the CLI can append/replace via replace_slot."""
    with session_scope() as session:
        rows = session.scalars(
            select(SiteSlotItem).where(SiteSlotItem.slot_key == key).order_by(SiteSlotItem.position)
        ).all()
        return [
            SlotItemIn(
                media_id=r.media_id,
                alt=r.alt_override,
                focal=Focal(x=r.focal_x, y=r.focal_y),
            )
            for r in rows
        ]
