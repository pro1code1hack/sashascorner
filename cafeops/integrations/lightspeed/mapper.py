"""API payload -> domain mapping. Never index a raw dict in service code.

Every external payload -- fixture or, one day, live -- is parsed through a
Pydantic v2 model first (`Raw*`). `ingest_sales.py` only ever sees the plain
`MappedSaleLine` dataclass defined here, never a `dict`.

Two research findings shape this file (full write-up with sources in the final
report; summarised here because they change the code):

1. **Modifiers carry no stable id on a sale line.** K-Series' "Get All Open
   Checks" and "Order notification" endpoints document a `modifiers` array of
   `{name, quantity}` only -- no modifier id, no price
   (https://api-docs.lsk.lightspeed.app/operation/operation-apegetcheck,
   https://api-docs.lsk.lightspeed.app/operation/operation-reservationordernotification).
   `Modifier.lightspeed_modifier_id` therefore cannot be the match key from a
   real payload today. `match_modifiers()` matches on normalised **name**, and
   only uses `lightspeed_modifier_id` opportunistically if a future endpoint
   (or `RawModifier.id`) ever supplies one.
2. **K-Series has no dedicated size field on a sale line or catalog item**
   (confirmed absent from the "Get Sales" and "Get business day sales"
   schemas). A café's size variants are ordinarily distinct catalog items
   whose *name* carries the size (e.g. "Latte (Medium)"), so `split_name_and_size`
   treats an explicit `size` field as a nicety when present and otherwise
   parses a trailing size token out of the name -- the path a real integration
   would actually need.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from cafeops.domain.types import SaleChannel, SizeCode

__all__ = [
    "CatalogRef",
    "DedupeOutcome",
    "MappedSaleLine",
    "MatchOutcome",
    "MenuItemRef",
    "RawCatalogBatch",
    "RawCatalogItem",
    "RawModifier",
    "RawReceipt",
    "RawReceiptBatch",
    "RawReceiptLine",
    "build_modifier_lookup",
    "dedupe_receipts",
    "map_receipt_lines",
    "match_menu_items",
    "normalize_text",
    "split_name_and_size",
]

# ==========================================================================
# Raw payload models
# ==========================================================================


class RawModifier(BaseModel):
    """One modifier on a sale line, as K-Series' checks/order-notification
    endpoints document it: name and quantity only."""

    model_config = ConfigDict(extra="ignore")

    name: str
    quantity: int = 1
    #: Not present in any documented sale-line payload. Kept optional in case
    #: a merchant-specific build or a future endpoint supplies one.
    id: str | None = None


class RawReceiptLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    #: The catalog item this line sold. K-Series' items are referenced by an
    #: id (or sku); fixtures carry both so the matcher can fall back.
    item_id: str | None = Field(default=None, alias="itemId")
    sku: str | None = None
    name: str
    name_override: str | None = Field(default=None, alias="nameOverride")
    size: str | None = None
    #: Decimal-as-string, never a JSON float (spec: quantities are exact).
    quantity: str = "1"
    total_amount_pence: int = Field(default=0, alias="totalAmountPence")
    void_reason: str | None = Field(default=None, alias="voidReason")
    is_refund: bool = Field(default=False, alias="isRefund")
    modifiers: list[RawModifier] = Field(default_factory=list)


class RawReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    closed_at: datetime = Field(alias="closedAt")
    channel: str = "EPOS"
    void_reason: str | None = Field(default=None, alias="voidReason")
    lines: list[RawReceiptLine] = Field(default_factory=list)


class RawReceiptBatch(BaseModel):
    """One page of the receipts/sales endpoint. `next_page` drives pagination."""

    model_config = ConfigDict(extra="ignore")

    receipts: list[RawReceipt] = Field(default_factory=list)
    next_page: str | None = Field(default=None, alias="nextPage")


class RawCatalogItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    sku: str | None = None
    name: str
    size: str | None = None
    active: bool = True


class RawCatalogBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[RawCatalogItem] = Field(default_factory=list)
    next_page: str | None = Field(default=None, alias="nextPage")


# ==========================================================================
# The same receipt, twice in one window
# ==========================================================================


@dataclass(frozen=True, slots=True)
class DedupeOutcome:
    """The result of collapsing a window's pages down to one copy per receipt."""

    receipts: tuple[RawReceipt, ...]
    #: Receipt ids that arrived more than once with IDENTICAL content. Counted, not
    #: complained about: overlapping pages re-deliver a receipt routinely, and
    #: `jobs/daily_sync.OVERLAP_DAYS` re-reads three days on purpose.
    duplicates: tuple[str, ...] = ()
    #: Receipt ids that arrived more than once with DIFFERENT content inside one
    #: window. Refused -- see `dedupe_receipts`.
    conflicts: tuple[str, ...] = ()


def _receipt_fingerprint(receipt: RawReceipt) -> tuple[object, ...]:
    """Everything about a receipt that ingestion would act on, in a comparable form.

    Deliberately not `model_dump()`: a field this system ignores (a till number, a
    staff name) must not turn a harmless re-delivery into a conflict.
    """
    return (
        receipt.closed_at,
        receipt.channel,
        receipt.void_reason,
        tuple(
            (
                line.id,
                line.item_id,
                line.sku,
                line.name_override or line.name,
                line.size,
                line.quantity,
                line.total_amount_pence,
                line.void_reason,
                line.is_refund,
                tuple(sorted((m.name, m.quantity) for m in line.modifiers)),
            )
            for line in receipt.lines
        ),
    )


def dedupe_receipts(receipts: Iterable[RawReceipt]) -> DedupeOutcome:
    """One copy per receipt id, and a refusal when two copies disagree.

    A receipt arriving twice in one window is normal -- pages overlap, and
    `daily_sync` re-reads three days deliberately. Collapsing identical copies is
    therefore silent-but-counted.

    Two copies of one receipt id that **differ** are a different matter, and are
    dropped from the window entirely rather than resolved. There is no rule for
    picking between them that is not a guess: page order is not settlement order, and
    "last one wins" would make the result depend on how the API chose to paginate.
    Dropping them costs one receipt and a loud line in the report. Guessing costs a
    wrong ingredient depletion that nothing downstream can detect -- and the
    correction path (`ingest_sales._emit_correction_adjustment`) exists for a change
    seen across two *runs*, where there is a real before and after, not for two
    contradictory versions inside one payload.
    """
    first: dict[str, RawReceipt] = {}
    fingerprints: dict[str, tuple[object, ...]] = {}
    duplicates: list[str] = []
    conflicts: list[str] = []

    for receipt in receipts:
        fingerprint = _receipt_fingerprint(receipt)
        if receipt.id not in first:
            first[receipt.id] = receipt
            fingerprints[receipt.id] = fingerprint
            continue
        if fingerprint == fingerprints[receipt.id]:
            if receipt.id not in duplicates:
                duplicates.append(receipt.id)
            continue
        if receipt.id not in conflicts:
            conflicts.append(receipt.id)

    return DedupeOutcome(
        receipts=tuple(r for rid, r in first.items() if rid not in conflicts),
        duplicates=tuple(duplicates),
        conflicts=tuple(conflicts),
    )


# ==========================================================================
# Name / size normalisation
# ==========================================================================

_SIZE_WORDS: dict[str, SizeCode] = {
    "s": SizeCode.S,
    "small": SizeCode.S,
    "m": SizeCode.M,
    "med": SizeCode.M,
    "medium": SizeCode.M,
    "reg": SizeCode.M,
    "regular": SizeCode.M,
    "xl": SizeCode.XL,
    "extralarge": SizeCode.XL,
    "large": SizeCode.XL,
    "l": SizeCode.XL,
    "one": SizeCode.ONE,
    "onesize": SizeCode.ONE,
}

#: Matches a trailing "(Medium)" / "(M)" / "- Small" style size token.
_TRAILING_SIZE_RE = re.compile(r"^(?P<base>.*?)\s*[(\-]\s*(?P<size>[A-Za-z ]+?)\)?\s*$")


def normalize_text(raw: str) -> str:
    """Lower-case, ASCII-fold, punctuation-collapsed form for name matching."""
    text = unicodedata.normalize("NFKD", raw)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_size_token(token: str) -> SizeCode | None:
    key = normalize_text(token).replace(" ", "")
    return _SIZE_WORDS.get(key)


def split_name_and_size(raw_name: str, explicit_size: str | None) -> tuple[str, SizeCode | None]:
    """Prefer an explicit size field; else parse a trailing size token out of the name."""
    if explicit_size:
        size = _parse_size_token(explicit_size)
        if size is not None:
            return raw_name.strip(), size
    match = _TRAILING_SIZE_RE.match(raw_name)
    if match:
        maybe_size = _parse_size_token(match.group("size"))
        if maybe_size is not None:
            return match.group("base").strip(), maybe_size
    return raw_name.strip(), None


# ==========================================================================
# Menu item matcher -- name + size is the natural key (spec / brief)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class MenuItemRef:
    """The minimal shape of a `cafeops.db.models.MenuItem` row the matcher needs."""

    id: int
    name: str
    size_code: SizeCode | None


@dataclass(frozen=True, slots=True)
class CatalogRef:
    lightspeed_id: str
    raw_name: str
    normalized_name: str
    size_code: SizeCode


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    #: menu_item_id -> lightspeed catalog id. The only thing ever written back.
    resolved: dict[int, str]
    #: Every menu item the matcher could NOT resolve -- reported, never guessed.
    unresolved_menu_items: tuple[str, ...]
    #: Catalog entries with no menu item at all. Informational, not an error.
    unmatched_catalog_entries: tuple[str, ...]
    #: Two-or-more catalog entries share one (name, size) key -- refuse to pick.
    ambiguous_menu_items: tuple[str, ...]

    def summary(self) -> str:
        return (
            f"matched {len(self.resolved)}, unresolved {len(self.unresolved_menu_items)}, "
            f"ambiguous {len(self.ambiguous_menu_items)}, "
            f"catalog-only {len(self.unmatched_catalog_entries)}"
        )


def _effective_size(size_code: SizeCode | None) -> SizeCode:
    """None means "no size in this system's identity" -- treated as ONE for
    matching, mirroring `seed/legacy.py::_size_code`'s own blank->ONE default."""
    return size_code or SizeCode.ONE


def _catalog_index(
    catalog: Iterable[RawCatalogItem],
) -> dict[tuple[str, SizeCode], list[CatalogRef]]:
    index: dict[tuple[str, SizeCode], list[CatalogRef]] = {}
    for raw in catalog:
        if not raw.active:
            continue
        base_name, size = split_name_and_size(raw.name, raw.size)
        key_size = _effective_size(size)
        norm = normalize_text(base_name)
        ref = CatalogRef(
            lightspeed_id=raw.id, raw_name=raw.name, normalized_name=norm, size_code=key_size
        )
        index.setdefault((norm, key_size), []).append(ref)
    return index


def match_menu_items(
    menu_items: Sequence[MenuItemRef], catalog: Iterable[RawCatalogItem]
) -> MatchOutcome:
    """Name + size is the natural key. Report everything unresolved -- never guess.

    An unmatched item silently depletes nothing, which is the worst possible
    failure because it looks like success (brief, verbatim). So: exact
    (normalized name, size) match only. No fuzzy/partial matching, ever.
    """
    index = _catalog_index(catalog)
    consumed: set[tuple[str, SizeCode]] = set()
    resolved: dict[int, str] = {}
    unresolved: list[str] = []
    ambiguous: list[str] = []

    for item in menu_items:
        key_size = _effective_size(item.size_code)
        norm = normalize_text(item.name)
        candidates = index.get((norm, key_size), [])
        if len(candidates) == 1:
            resolved[item.id] = candidates[0].lightspeed_id
            consumed.add((norm, key_size))
        elif not candidates:
            unresolved.append(
                f"{item.name!r} [{key_size.value}] (menu_item_id={item.id}): "
                "no catalog item with this name+size"
            )
        else:
            ids = ", ".join(c.lightspeed_id for c in candidates)
            ambiguous.append(
                f"{item.name!r} [{key_size.value}] (menu_item_id={item.id}): "
                f"{len(candidates)} catalog entries match ({ids}) -- refusing to guess"
            )

    unmatched_catalog = [
        f"{ref.raw_name!r} ({ref.lightspeed_id}) [{ref.size_code.value}]: "
        "no menu_item with this name+size"
        for key, refs in index.items()
        if key not in consumed
        for ref in refs
    ]

    return MatchOutcome(
        resolved=resolved,
        unresolved_menu_items=tuple(sorted(unresolved)),
        unmatched_catalog_entries=tuple(sorted(unmatched_catalog)),
        ambiguous_menu_items=tuple(sorted(ambiguous)),
    )


def build_modifier_lookup(modifiers: Iterable[tuple[int, str]]) -> dict[str, int]:
    """normalized modifier name -> modifier.id.

    Name is the only reliable key available on a real sale line (finding 1
    above). `lightspeed_modifier_id` stays in the schema for a future endpoint
    that might supply one; nothing here fabricates one.
    """
    return {normalize_text(name): mid for mid, name in modifiers}


# ==========================================================================
# Sale line mapping
# ==========================================================================


@dataclass(frozen=True, slots=True)
class MappedSaleLine:
    lightspeed_receipt_id: str
    lightspeed_line_id: str
    #: None when the matcher could not resolve this line's item -- the caller
    #: must report it, never guess a menu_item_id.
    menu_item_id: int | None
    raw_item_name: str
    raw_size: str | None
    qty: Decimal
    gross_pence: int
    sold_at: datetime
    channel: SaleChannel
    applied_modifier_ids: tuple[int, ...]
    unmatched_modifier_names: tuple[str, ...]
    voided: bool
    is_refund: bool


def _channel(raw: str | None) -> SaleChannel:
    if not raw:
        return SaleChannel.EPOS
    try:
        return SaleChannel[raw.strip().upper()]
    except KeyError:
        return SaleChannel.OTHER


def _parse_qty(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def map_receipt_lines(
    receipts: Iterable[RawReceipt],
    *,
    item_lookup: Mapping[str, int],
    modifier_lookup: Mapping[str, int],
) -> list[MappedSaleLine]:
    """Raw receipts -> `MappedSaleLine`s.

    `item_lookup` maps a catalog item id OR sku to a `menu_item_id` (built from
    `MatchOutcome.resolved` by the caller). `modifier_lookup` maps a normalized
    modifier name to a `modifier.id` (see `match_modifiers`).
    """
    out: list[MappedSaleLine] = []
    for receipt in receipts:
        receipt_voided = receipt.void_reason is not None
        channel = _channel(receipt.channel)
        for line in receipt.lines:
            qty = _parse_qty(line.quantity)
            is_refund = line.is_refund or qty < 0

            menu_item_id: int | None = None
            if line.item_id is not None:
                menu_item_id = item_lookup.get(line.item_id)
            if menu_item_id is None and line.sku is not None:
                menu_item_id = item_lookup.get(line.sku)

            modifier_ids: list[int] = []
            unmatched: list[str] = []
            for mod in line.modifiers:
                key = normalize_text(mod.name)
                matched_id = modifier_lookup.get(key)
                if matched_id is None:
                    unmatched.append(mod.name)
                else:
                    modifier_ids.append(matched_id)

            out.append(
                MappedSaleLine(
                    lightspeed_receipt_id=receipt.id,
                    lightspeed_line_id=line.id,
                    menu_item_id=menu_item_id,
                    raw_item_name=line.name_override or line.name,
                    raw_size=line.size,
                    qty=qty,
                    gross_pence=line.total_amount_pence,
                    sold_at=receipt.closed_at,
                    channel=channel,
                    applied_modifier_ids=tuple(modifier_ids),
                    unmatched_modifier_names=tuple(unmatched),
                    voided=receipt_voided or line.void_reason is not None,
                    is_refund=is_refund,
                )
            )
    return out
