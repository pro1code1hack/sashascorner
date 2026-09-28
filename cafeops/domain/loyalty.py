"""Sasha's Corner Rewards: the rules, with no database and no clock of their own.

docs/loyalty/SPEC.md "Rules, fraud and privacy", fitted by CONTRACT.md. Everything here is
a pure function of its arguments -- no SQLAlchemy, no I/O, no `config` import -- so the
arithmetic that decides whether somebody gets a free drink is the same on the till, in the
birthday job and in the admin adjust, and can be read without a session in one's head.

Four families:

1. **The signed QR.** `SC1:<card_id>:<hmac8>`. The pass shows it, the scanner parses it.
   A tampered or invented payload fails the signature, so a screenshot of a made-up card
   id cannot be stamped.
2. **Stamp arithmetic.** Carry-over above `stamps_required` stays on the card, one reward
   per full card. `stamps_current` is therefore always below `stamps_required` afterwards.
3. **Guards.** The 10-minute cooldown and the per-staff hourly alert.
4. **Calendar rules.** The birthday window (seven days either side, once a year, and not
   for a birthday typed in fewer than 30 days before it), and the 24-month retention.

Contact normalisation lives here too: "one card per email or phone" is only a rule if two
spellings of the same number compare equal.

Phase 3 adds three more, still pure:

5. **Eligibility.** Which menu items a programme (or a reward-catalogue entry) counts:
   a base scope ("drinks" -- the phase-1 rule, decided by the service because it needs
   recipes -- or "all") narrowed by categories, name keywords and drink templates.
   Keywords exist because the live menu's categories are mostly empty.
6. **Points.** `points_for_spend`: whole points per whole pound, integer maths on pence,
   rounded down, so 0.6 of a point is never invented.
7. **What a receipt earns** (`receipt_units`) and the **dedupe window** against staff
   scans (`staff_scan_covers`).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from calendar import isleap
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

__all__ = [
    "BIRTHDAY_NOTICE_DAYS",
    "COOLDOWN_MAX_STAMPS",
    "COOLDOWN_MINUTES",
    "HOURLY_ALERT_THRESHOLD",
    "MAX_POINTS_SPEND_PENCE",
    "PROMO_LIMIT_PER_MONTH",
    "QR_PREFIX",
    "RETENTION_MONTHS",
    "UNDO_SECONDS",
    "BirthdayDecision",
    "Eligibility",
    "ItemFacts",
    "ReceiptLine",
    "StampOutcome",
    "apply_stamps",
    "birthday_decision",
    "birthday_in_year",
    "contact_hash",
    "cooldown_exceeded",
    "is_valid_birthday",
    "looks_like_email",
    "mask_contact",
    "months_before",
    "normalise_email",
    "normalise_phone",
    "over_hourly_threshold",
    "parse_qr_payload",
    "points_for_spend",
    "qr_payload",
    "receipt_units",
    "staff_scan_covers",
    "unsubscribe_signature",
    "verify_unsubscribe",
]

QR_PREFIX = "SC1"
#: SPEC: ">3 stamps on one card within 10 minutes needs a manager PIN".
COOLDOWN_MAX_STAMPS = 3
COOLDOWN_MINUTES = 10
#: SPEC: "one staff member >20 stamps an hour" is the example of an unusual pattern.
HOURLY_ALERT_THRESHOLD = 20
#: SPEC: "Undo only within 2 minutes; later corrections manager-only with a reason."
UNDO_SECONDS = 120
#: SPEC/PECR: lock-screen messages limited to card updates and at most 2 promos a month.
PROMO_LIMIT_PER_MONTH = 2
#: SPEC privacy: "deleted 24 months after last activity".
RETENTION_MONTHS = 24
#: SPEC: "From 7 days before to 7 days after the birthday".
BIRTHDAY_NOTICE_DAYS = 7
#: SPEC: "a birthday entered/changed <30 days before the date doesn't qualify that year".
BIRTHDAY_MIN_LEAD_DAYS = 30


# --------------------------------------------------------------------------
# QR
# --------------------------------------------------------------------------


def _hmac8(card_id: str, qr_secret: str, key: bytes) -> str:
    msg = f"{card_id}:{qr_secret}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:8]


def qr_payload(card_id: str, qr_secret: str, key: bytes) -> str:
    """`SC1:<card_id>:<hmac8>` -- what the pass barcode and the web card both show.

    The per-card `qr_secret` is inside the MAC, so rotating one card's secret kills every
    screenshot of that card without touching anyone else's.
    """
    return f"{QR_PREFIX}:{card_id}:{_hmac8(card_id, qr_secret, key)}"


def parse_qr_payload(
    payload: str, lookup_secret: Callable[[str], str | None], key: bytes
) -> str | None:
    """The card id when the signature verifies, else None. Constant-time compare.

    `lookup_secret(card_id)` returns that card's `qr_secret` or None if there is no such
    card. An unknown card and a bad signature both answer None here; the scanner route
    tells them apart itself, because "that is not our card" and "that card was forged" are
    different conversations at a till.
    """
    parts = payload.strip().split(":")
    if len(parts) != 3 or parts[0] != QR_PREFIX:
        return None
    _, card_id, presented = parts
    if not card_id or len(presented) != 8:
        return None
    secret = lookup_secret(card_id)
    if secret is None:
        return None
    expected = _hmac8(card_id, secret, key)
    if not hmac.compare_digest(expected, presented.lower()):
        return None
    return card_id


# --------------------------------------------------------------------------
# stamps
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StampOutcome:
    """What a stamp event does to a card.

    `rewards_issued` is how many full cards the new total made. Usually 0 or 1; a paper
    migration of 7 on a card already at 7 is still exactly one, with 6 carried over, and
    a large manual correction can in principle cross more than one.
    """

    stamps_after: int
    rewards_issued: int


def apply_stamps(current: int, delta: int, required: int) -> StampOutcome:
    """Add `delta` to a card at `current`, issuing a reward per full card.

    Raises `ValueError` if the result would be negative: a correction that takes away
    stamps the card does not hold is a mistake to show the manager, not a number to clamp,
    because clamping would silently forgive whatever the correction was meant to reverse.
    """
    if required < 1:
        raise ValueError("stamps_required must be at least 1")
    if delta == 0:
        raise ValueError("a stamp event must change the count")
    total = current + delta
    if total < 0:
        raise ValueError(f"that would leave the card at {total} stamps; it holds {current}")
    issued, after = divmod(total, required)
    return StampOutcome(stamps_after=after, rewards_issued=issued)


def cooldown_exceeded(stamps_in_window: int, delta: int) -> bool:
    """True when this stamp would take the card past the cooldown without a manager.

    `stamps_in_window` is the card's net purchase stamps in the last `COOLDOWN_MINUTES`.
    Three stamps in one scan for a group order is allowed on its own; a fourth within ten
    minutes is the pattern the spec asks a manager to look at.
    """
    return stamps_in_window + delta > COOLDOWN_MAX_STAMPS


def over_hourly_threshold(stamps_by_staff_last_hour: int) -> bool:
    return stamps_by_staff_last_hour > HOURLY_ALERT_THRESHOLD


# --------------------------------------------------------------------------
# birthdays
# --------------------------------------------------------------------------


def is_valid_birthday(day: int, month: int) -> bool:
    """Day and month, no year (SPEC privacy). 29 February is allowed."""
    if not 1 <= month <= 12:
        return False
    days_in = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return 1 <= day <= days_in


def birthday_in_year(day: int, month: int, year: int) -> date:
    """The birthday's date in `year`. A 29 February birthday is 28 February otherwise."""
    if month == 2 and day == 29 and not isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


@dataclass(frozen=True, slots=True)
class BirthdayDecision:
    """Whether to issue a birthday reward today, and for which occurrence.

    `birthday_year` is the calendar year of the birthday being celebrated -- the key of the
    once-a-year rule. It is not always today's year: on 28 December a 2 January birthday
    belongs to next year's occurrence.
    """

    issue: bool
    birthday_year: int
    birthday: date
    window_opens: date
    expires_on: date
    reason: str


def birthday_decision(
    day: int, month: int, *, today: date, birthday_set_on: date | None
) -> BirthdayDecision:
    """Is `today` inside a birthday window that qualifies? Pure; the job checks once-a-year.

    The window is [birthday - 7, birthday + 7] inclusive. The nearest occurrence to today
    is used, so windows that cross New Year work in both directions.

    The 30-day rule: a birthday recorded (or changed) fewer than 30 days before the date
    does not qualify for that year's occurrence. Without it, typing tomorrow's date at
    sign-up is a free drink on demand.
    """
    candidates = [birthday_in_year(day, month, today.year + k) for k in (-1, 0, 1)]
    nearest = min(candidates, key=lambda d: abs((d - today).days))
    opens = nearest - timedelta(days=BIRTHDAY_NOTICE_DAYS)
    closes = nearest + timedelta(days=BIRTHDAY_NOTICE_DAYS)
    if not opens <= today <= closes:
        return BirthdayDecision(False, nearest.year, nearest, opens, closes, "outside the window")
    if birthday_set_on is not None and (nearest - birthday_set_on).days < BIRTHDAY_MIN_LEAD_DAYS:
        return BirthdayDecision(
            False,
            nearest.year,
            nearest,
            opens,
            closes,
            f"birthday recorded {birthday_set_on.isoformat()}, under "
            f"{BIRTHDAY_MIN_LEAD_DAYS} days before {nearest.isoformat()}",
        )
    return BirthdayDecision(True, nearest.year, nearest, opens, closes, "in the window")


def months_before(day: date, months: int) -> date:
    """`day` minus whole calendar months, clamped to the month's last day.

    For the 24-month retention: "24 months after last activity" is a calendar statement,
    and 730 days drifts by a day across a leap year.
    """
    total = day.year * 12 + (day.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    last = (31, 29 if isleap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return date(year, month, min(day.day, last))


# --------------------------------------------------------------------------
# contacts
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def looks_like_email(raw: str) -> bool:
    return "@" in raw


def normalise_email(raw: str) -> str | None:
    """Lower-cased and trimmed, or None if it is not plausibly an address.

    Lower-casing the local part is technically lossy, but no mail provider a café customer
    uses treats case as significant, and "one card per email" must not be defeated by a
    capital letter.
    """
    value = raw.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        return None
    return value


def normalise_phone(raw: str, *, default_country: str = "44") -> str | None:
    """E.164 (`+447700900123`), UK by default, or None if it cannot be a number.

    Accepts what people type: spaces, dashes, brackets, a leading 0 for a UK number, `00`
    for an international prefix, and `44...` without the plus.
    """
    cleaned = re.sub(r"[\s\-().]", "", raw.strip())
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        digits = cleaned[1:]
    elif cleaned.startswith("00"):
        digits = cleaned[2:]
    elif cleaned.startswith("0"):
        digits = default_country + cleaned[1:]
    elif cleaned.startswith(default_country) and len(cleaned) >= 11:
        digits = cleaned
    else:
        digits = default_country + cleaned
    if not digits.isdigit():
        return None
    # A UK trunk "0" typed after +44 ("+44 07700...") is the commonest slip there is.
    if digits.startswith("440"):
        digits = "44" + digits[3:]
    if not 8 <= len(digits) <= 15:
        return None
    return "+" + digits


def mask_contact(email: str | None, phone: str | None) -> str:
    """Enough for the customer at the till to recognise, not enough to read out.

    `s***@gmail.com` / `+44 **** **0123`.
    """
    if phone:
        return f"{phone[:3]} **** **{phone[-4:]}"
    if email:
        local, _, domain = email.partition("@")
        return f"{local[:1]}***@{domain}"
    return "(no contact)"


# --------------------------------------------------------------------------
# unsubscribe links
# --------------------------------------------------------------------------


def unsubscribe_signature(member_id: int, key: bytes) -> str:
    """The `s=` of an unsubscribe link. Only this member's link can opt this member out.

    Unguessable per member so that the link needs no login (PECR: unsubscribing must be
    simple) and cannot be walked by incrementing `m=`.
    """
    msg = f"unsubscribe:{member_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:24]


def verify_unsubscribe(member_id: int, signature: str, key: bytes) -> bool:
    return hmac.compare_digest(unsubscribe_signature(member_id, key), signature.strip().lower())


# --------------------------------------------------------------------------
# phase 3: eligibility, points, receipts
# --------------------------------------------------------------------------

#: A spend typed at the till above this needs a manager's PIN: a slipped zero on £4.50
#: would be 4,500 points.
MAX_POINTS_SPEND_PENCE = 5000

_SCOPES = ("drinks", "all")


def _clean_words(values: Iterable[Any], *, limit: int = 20) -> tuple[str, ...]:
    out: list[str] = []
    for v in values:
        text = " ".join(str(v).split()).lower()[:60]
        if text and text not in out:
            out.append(text)
    return tuple(out[:limit])


@dataclass(frozen=True, slots=True)
class Eligibility:
    """What counts, as stored in `loyalty_program.eligibility` / `loyalty_reward_option`.

    An item is eligible when it is in `scope` AND, if any filter is given, it matches at
    least one of them (a category, a keyword in its name, or a template). No filter means
    the whole scope. `scope="drinks"` is the phase-1 drinks rule
    (`services/loyalty/redeem.list_drinks`); `"all"` is every active menu item, for a
    "slice of cake" reward.
    """

    scope: str = "drinks"
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    template_ids: tuple[int, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, raw: Mapping[str, Any] | None) -> Eligibility:
        """Tolerant of anything stored: unknown keys are ignored, bad values dropped."""
        if not raw:
            return cls()
        scope = str(raw.get("scope") or "drinks").lower()
        if scope not in _SCOPES:
            scope = "drinks"
        templates: list[int] = []
        for t in raw.get("template_ids") or ():
            try:
                templates.append(int(t))
            except (TypeError, ValueError):
                continue
        return cls(
            scope=scope,
            categories=_clean_words(raw.get("categories") or ()),
            keywords=_clean_words(raw.get("keywords") or ()),
            template_ids=tuple(sorted(set(templates)))[:50],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "categories": list(self.categories),
            "keywords": list(self.keywords),
            "template_ids": list(self.template_ids),
        }

    @property
    def is_default(self) -> bool:
        return self == Eligibility()

    @property
    def filtered(self) -> bool:
        return bool(self.categories or self.keywords or self.template_ids)

    def describe(self) -> str:
        """For people: "drinks with 'matcha' in the name"."""
        base = "any drink" if self.scope == "drinks" else "anything on the menu"
        if not self.filtered:
            return base
        parts: list[str] = []
        if self.keywords:
            parts.append("with " + " or ".join(f"'{k}'" for k in self.keywords) + " in the name")
        if self.categories:
            parts.append("in " + ", ".join(self.categories))
        if self.template_ids:
            parts.append(f"from {len(self.template_ids)} recipe template(s)")
        noun = "drinks" if self.scope == "drinks" else "items"
        return f"{noun} " + " or ".join(parts)


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """What eligibility needs to know about one menu item."""

    menu_item_id: int
    name: str
    category: str | None
    template_id: int | None
    is_drink: bool


def item_matches(rule: Eligibility, item: ItemFacts) -> bool:
    if rule.scope == "drinks" and not item.is_drink:
        return False
    if not rule.filtered:
        return True
    name = " ".join(item.name.lower().split())
    if any(k in name for k in rule.keywords):
        return True
    if item.category and item.category.strip().lower() in rule.categories:
        return True
    return item.template_id is not None and item.template_id in rule.template_ids


def points_for_spend(spend_pence: int, points_per_pound: int) -> int:
    """Whole points for a spend: `spend * rate // 100`. Never negative, never rounded up."""
    if spend_pence <= 0 or points_per_pound <= 0:
        return 0
    return spend_pence * points_per_pound // 100


@dataclass(frozen=True, slots=True)
class ReceiptLine:
    """One sale line as the auto-stamper sees it (signed qty: a refund is negative)."""

    menu_item_id: int
    qty: int
    gross_pence: int
    voided: bool
    eligible: bool


def receipt_units(
    lines: Iterable[ReceiptLine],
    *,
    kind: str,
    max_per_receipt: int,
    points_per_pound: int | None,
) -> int:
    """What one receipt is worth on one card, voids and refunds netted, never negative.

    STAMPS: one per eligible item (qty, refunds subtract), capped at `max_per_receipt`
    (the scanner's "+3 at most per scan" -- a receipt is one visit). POINTS: whole points
    on the eligible spend. A fully refunded or voided receipt is worth 0.
    """
    live = [line for line in lines if line.eligible and not line.voided]
    if kind == "POINTS":
        spend = sum(line.gross_pence for line in live)
        return points_for_spend(spend, points_per_pound or 0)
    count = sum(line.qty for line in live)
    return max(0, min(count, max_per_receipt))


def staff_scan_covers(receipt_at: datetime, scans_at: Iterable[datetime], minutes: int) -> bool:
    """True when a staff scan on the card falls within `minutes` either side of the receipt.

    Either side because a scan before paying and a receipt closed after it are the same
    visit, and so is a card shown after the receipt printed.
    """
    window = timedelta(minutes=max(0, minutes))
    return any(abs(at - receipt_at) <= window for at in scans_at)


def contact_hash(value: str, key: bytes) -> str:
    """HMAC of a NORMALISED email or phone, so receipts can be matched without storing it."""
    return hmac.new(key, f"contact:{value}".encode(), hashlib.sha256).hexdigest()
