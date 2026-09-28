"""Programmes (phase 3): several at once, stamps or points, what earns, what a reward can be.

docs/loyalty/CONTRACT.md "Phase 3". The one programme phase 1 shipped (`slug = "stamp"`) is
still THE card: joining gives it, recovery returns it, and `default_program` still means
it. Others ("Matcha club") are extra cards a member can add; each is its own card row,
its own QR and its own wallet pass (one card per member per programme -- the unique key
on `loyalty_card` already said so).

What lives here:

- **Reading programmes** (`programs`, `program_by_slug`, `unit_word`) and their reward
  catalogues (`reward_options`).
- **Eligibility against the real menu.** `domain.loyalty.Eligibility` is the pure rule;
  this module supplies the facts it needs (`item_facts`), in particular "is this a
  drink", which is the phase-1 SQL rule in `redeem.drink_condition` and cannot be pure.
  `eligible_menu` is the picker list for a reward option.
- **Editing** (`apply_change`, `create_program`): the one place a programme's rules
  change, used by the old `/api/members/program` PUT and by the phase-3 editor alike.
  Every rule that would strand a customer's stamps is refused, not clamped: lowering a
  target below what a card already holds, and switching STAMPS <-> POINTS once anybody
  has earned anything (a stamp is not a point, and converting would be a guess).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyProgram,
    LoyaltyRewardOption,
    LoyaltyStampEvent,
    MenuItem,
    ProgramKind,
)
from cafeops.domain.loyalty import Eligibility, ItemFacts, item_matches
from cafeops.services.loyalty.common import now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.redeem import DrinkOption, drink_condition, item_label

__all__ = [
    "OptionChange",
    "ProgramChange",
    "apply_change",
    "create_program",
    "eligible_menu",
    "item_facts",
    "option_rule",
    "program_by_id",
    "program_by_slug",
    "program_rule",
    "programs",
    "reward_options",
    "unit_word",
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


def programs(session: Session, *, active_only: bool = False) -> list[LoyaltyProgram]:
    stmt = select(LoyaltyProgram).order_by(LoyaltyProgram.sort_order, LoyaltyProgram.id)
    if active_only:
        stmt = stmt.where(LoyaltyProgram.active.is_(True))
    return list(session.scalars(stmt))


def program_by_slug(session: Session, slug: str) -> LoyaltyProgram:
    program = session.scalar(select(LoyaltyProgram).where(LoyaltyProgram.slug == slug.strip()))
    if program is None:
        raise LoyaltyError(404, "unknown_program", "There is no rewards programme with that name.")
    return program


def program_by_id(session: Session, program_id: int) -> LoyaltyProgram:
    program = session.get(LoyaltyProgram, program_id)
    if program is None:
        raise LoyaltyError(404, "unknown_program", "There is no rewards programme with that id.")
    return program


def unit_word(program: LoyaltyProgram, n: int | None = None) -> str:
    """ "stamp"/"stamps" or "point"/"points"."""
    word = "point" if program.kind is ProgramKind.POINTS else "stamp"
    return word if n == 1 else f"{word}s"


def program_rule(program: LoyaltyProgram) -> Eligibility:
    return Eligibility.parse(program.eligibility)


def option_rule(option: LoyaltyRewardOption) -> Eligibility:
    return Eligibility.parse(option.eligibility)


def reward_options(
    session: Session, program_id: int, *, active_only: bool = True
) -> list[LoyaltyRewardOption]:
    stmt = (
        select(LoyaltyRewardOption)
        .where(LoyaltyRewardOption.program_id == program_id)
        .order_by(LoyaltyRewardOption.sort_order, LoyaltyRewardOption.id)
    )
    if active_only:
        stmt = stmt.where(LoyaltyRewardOption.active.is_(True))
    return list(session.scalars(stmt))


# --------------------------------------------------------------------------
# eligibility against the menu
# --------------------------------------------------------------------------


def item_facts(session: Session, menu_item_ids: Iterable[int]) -> dict[int, ItemFacts]:
    """Facts for these items, active or not: a receipt can name an item since retired."""
    ids = sorted(set(menu_item_ids))
    if not ids:
        return {}
    drinks = set(
        session.scalars(select(MenuItem.id).where(MenuItem.id.in_(ids), drink_condition(now_utc())))
    )
    return {
        item.id: ItemFacts(
            menu_item_id=item.id,
            name=item.name,
            category=item.category,
            template_id=item.template_id,
            is_drink=item.id in drinks,
        )
        for item in session.scalars(select(MenuItem).where(MenuItem.id.in_(ids)))
    }


def eligible_menu(session: Session, rule: Eligibility) -> list[DrinkOption]:
    """Active menu items the rule allows, one row per size, for the reward picker."""
    stmt = select(MenuItem).where(MenuItem.active.is_(True))
    if rule.scope == "drinks":
        stmt = stmt.where(drink_condition(now_utc()))
    items = list(session.scalars(stmt.order_by(MenuItem.name, MenuItem.price_pence)))
    facts = item_facts(session, (i.id for i in items)) if rule.filtered else {}
    out: list[DrinkOption] = []
    for item in items:
        if rule.filtered and not item_matches(rule, facts[item.id]):
            continue
        out.append(
            DrinkOption(
                menu_item_id=item.id,
                name=item_label(item),
                category=item.category,
                price_pence=item.price_pence or None,
            )
        )
    return out


# --------------------------------------------------------------------------
# editing
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptionChange:
    """One catalogue entry as the editor sends it. No `id` = a new entry."""

    name: str
    id: int | None = None
    description: str | None = None
    eligibility: dict[str, Any] | None = None
    max_price_pence: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class ProgramChange:
    """Fields to change; None = leave alone. `clear_price_cap` because None is taken."""

    name: str | None = None
    stamps_required: int | None = None
    max_stamps_per_scan: int | None = None
    reward_text: str | None = None
    reward_max_price_pence: int | None = None
    clear_price_cap: bool = False
    birthday_reward: bool | None = None
    referral_stamps: int | None = None
    active: bool | None = None
    kind: ProgramKind | None = None
    points_per_pound: int | None = None
    eligibility: dict[str, Any] | None = None
    #: True = store the default (drinks) eligibility, i.e. NULL.
    clear_eligibility: bool = False
    description: str | None = None
    reward_ready_label: str | None = None
    sort_order: int | None = None
    #: Cooldown: more than `cooldown_max_stamps` purchase stamps on one card within
    #: `cooldown_minutes` needs a manager's PIN at the scanner.
    cooldown_max_stamps: int | None = None
    cooldown_minutes: int | None = None
    #: The whole catalogue, in order. None = leave it alone; entries missing from a given
    #: list are retired (`active = false`), never deleted -- redeemed rewards point at them.
    reward_options: Sequence[OptionChange] | None = None


def _has_ledger(session: Session, program_id: int) -> bool:
    return (
        session.scalar(
            select(func.count(LoyaltyStampEvent.id))
            .join(LoyaltyCard, LoyaltyCard.id == LoyaltyStampEvent.card_id)
            .where(LoyaltyCard.program_id == program_id)
        )
        or 0
    ) > 0


def _bad(detail: str) -> LoyaltyError:
    return LoyaltyError(422, "bad_value", detail)


def _save_options(
    session: Session, program: LoyaltyProgram, options: Sequence[OptionChange]
) -> None:
    existing = {o.id: o for o in reward_options(session, program.id, active_only=False)}
    kept: set[int] = set()
    for order, change in enumerate(options):
        name = " ".join(change.name.split())[:80]
        if not name:
            raise _bad("Every reward in the catalogue needs a name.")
        if change.max_price_pence is not None and change.max_price_pence < 0:
            raise _bad(f"{name}: a price cap cannot be negative.")
        rule = Eligibility.parse(change.eligibility)
        stored = None if rule.is_default else rule.to_json()
        if change.id is not None:
            row = existing.get(change.id)
            if row is None:
                raise LoyaltyError(
                    404, "unknown_option", f"Reward option {change.id} is not in this programme."
                )
        else:
            row = LoyaltyRewardOption(program_id=program.id, created_at=now_utc())
            session.add(row)
        row.name = name
        row.description = (change.description or "").strip()[:200] or None
        row.eligibility = stored
        row.max_price_pence = change.max_price_pence
        row.active = change.active
        row.sort_order = order
        session.flush()
        kept.add(row.id)
    for row_id, row in existing.items():
        if row_id not in kept:
            row.active = False


def apply_change(session: Session, program: LoyaltyProgram, change: ProgramChange) -> None:
    """Validate and apply. Raises `LoyaltyError` before anything is half-applied only for
    the checks that need the database; field checks come first."""
    kind = change.kind or program.kind
    if (
        change.kind is not None
        and change.kind is not program.kind
        and _has_ledger(session, program.id)
    ):
        raise LoyaltyError(
            409,
            "kind_locked",
            "Members have already earned on this programme. Stamps and points are not the "
            "same thing, so it cannot switch; start a new programme instead.",
        )
    if change.name is not None:
        if not change.name.strip():
            raise LoyaltyError(422, "name_required", "The programme needs a name.")
        program.name = change.name.strip()[:80]
    if change.stamps_required is not None:
        low, high = (10, 10000) if kind is ProgramKind.POINTS else (2, 20)
        if not low <= change.stamps_required <= high:
            what = "points" if kind is ProgramKind.POINTS else "stamps"
            raise _bad(f"A reward needs between {low} and {high} {what}.")
        blocking = session.scalar(
            select(func.count(LoyaltyCard.id)).where(
                LoyaltyCard.program_id == program.id,
                LoyaltyCard.voided_at.is_(None),
                LoyaltyCard.stamps_current >= change.stamps_required,
            )
        )
        if blocking:
            raise LoyaltyError(
                409,
                "cards_above_new_target",
                f"{blocking} card(s) already hold {change.stamps_required} or more. "
                "Lowering the target would leave them without the reward they earned.",
            )
        program.stamps_required = change.stamps_required
    elif change.kind is not None and change.kind is not program.kind:
        # A new kind with the old target: 8 points or 100 stamps are both nonsense.
        program.stamps_required = 100 if kind is ProgramKind.POINTS else 8
    if change.max_stamps_per_scan is not None:
        if not 1 <= change.max_stamps_per_scan <= 10:
            raise _bad("Stamps per scan must be 1 to 10.")
        program.max_stamps_per_scan = change.max_stamps_per_scan
    if change.reward_text is not None:
        if not change.reward_text.strip():
            raise _bad("Say what the reward is.")
        program.reward_text = change.reward_text.strip()[:120]
    if change.clear_price_cap:
        program.reward_max_price_pence = None
    elif change.reward_max_price_pence is not None:
        if change.reward_max_price_pence < 0:
            raise _bad("A price cap cannot be negative.")
        program.reward_max_price_pence = change.reward_max_price_pence
    if change.birthday_reward is not None:
        program.birthday_reward = change.birthday_reward
    if change.referral_stamps is not None:
        if not 0 <= change.referral_stamps <= 8:
            raise _bad("Referral stamps must be 0 to 8.")
        program.referral_stamps = change.referral_stamps
    if change.active is not None:
        program.active = change.active
    if kind is ProgramKind.POINTS:
        rate = (
            change.points_per_pound
            if change.points_per_pound is not None
            else program.points_per_pound
        )
        if rate is None or not 1 <= rate <= 100:
            raise _bad("Points per pound must be 1 to 100.")
        program.points_per_pound = rate
    else:
        program.points_per_pound = None
    program.kind = kind
    if change.clear_eligibility:
        program.eligibility = None
    elif change.eligibility is not None:
        rule = Eligibility.parse(change.eligibility)
        program.eligibility = None if rule.is_default else rule.to_json()
    if change.description is not None:
        program.description = change.description.strip()[:200] or None
    if change.reward_ready_label is not None:
        if not change.reward_ready_label.strip():
            raise _bad("Say what the card shows when a reward is ready.")
        program.reward_ready_label = change.reward_ready_label.strip()[:60]
    if change.cooldown_max_stamps is not None:
        if not 1 <= change.cooldown_max_stamps <= 20:
            raise _bad("The cooldown allows 1 to 20 stamps before a manager is needed.")
        program.cooldown_max_stamps = change.cooldown_max_stamps
    if change.cooldown_minutes is not None:
        if not 1 <= change.cooldown_minutes <= 240:
            raise _bad("The cooldown window is 1 to 240 minutes.")
        program.cooldown_minutes = change.cooldown_minutes
    if change.sort_order is not None:
        program.sort_order = max(0, min(change.sort_order, 999))
    if program.kind is ProgramKind.STAMPS and (
        program.cooldown_max_stamps < program.max_stamps_per_scan
    ):
        raise _bad(
            f"The cooldown ({program.cooldown_max_stamps}) cannot be lower than the most "
            f"stamps in one scan ({program.max_stamps_per_scan}), or a full scan would "
            "always need a manager."
        )
    if change.reward_options is not None:
        _save_options(session, program, change.reward_options)
    session.flush()


def create_program(session: Session, *, slug: str, change: ProgramChange) -> LoyaltyProgram:
    """A new programme, inactive unless `change.active` says otherwise."""
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise _bad(
            "The short name is 3 to 40 lower-case letters, digits and dashes, e.g. matcha-club."
        )
    if session.scalar(select(LoyaltyProgram.id).where(LoyaltyProgram.slug == slug)) is not None:
        raise LoyaltyError(409, "slug_taken", "A programme with that short name exists already.")
    if not change.name or not change.reward_text:
        raise _bad("A new programme needs a name and a reward.")
    kind = change.kind or ProgramKind.STAMPS
    program = LoyaltyProgram(
        slug=slug,
        name=change.name.strip()[:80],
        kind=kind,
        stamps_required=100 if kind is ProgramKind.POINTS else 8,
        points_per_pound=10 if kind is ProgramKind.POINTS else None,
        max_stamps_per_scan=3,
        reward_text=change.reward_text.strip()[:120],
        birthday_reward=False,
        referral_stamps=0,
        active=False,
        reward_ready_label="Reward ready",
        sort_order=len(programs(session)),
        created_at=now_utc(),
    )
    session.add(program)
    session.flush()
    apply_change(session, program, change)
    return program
