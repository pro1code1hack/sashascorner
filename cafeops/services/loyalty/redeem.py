"""Giving the free drink, and the drinks the till may give (SPEC user flow 3, CONTRACT §7).

**A redemption is a £0 sale.** When staff say which drink it was, a `sale` row is written
(qty 1, gross 0, receipt `"loyalty"`, line `"loyalty-reward-<id>"`, channel OTHER), and the
nightly expansion depletes its recipe like any sale. Without it the milk and beans in
every free drink would read as drift, and the drift gate would blame the recipe
(CLAUDE.md §5.2) for what was really generosity.

The stamp ledger is untouched: the stamps were consumed when the reward was issued, and
the reward row (`redeemed_at`, `redeemed_menu_item_id`, `sale_id`, `staff_user_id`) is
the audit.

**Undo** (2 minutes, same device or any manager) clears the redemption and VOIDS the sale
rather than deleting it -- `sale` rows are never deleted (a re-sync must be a no-op, not
a resurrection). Redeeming the same reward again reuses that line, since its id is unique.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cafeops.db.models import (
    Ingredient,
    LoyaltyAudit,
    LoyaltyCard,
    LoyaltyReward,
    LoyaltyRewardOption,
    ManualRecipeLine,
    MenuCategory,
    MenuItem,
    MenuKind,
    Sale,
    SaleChannel,
    SizeCode,
)
from cafeops.domain.loyalty import UNDO_SECONDS, Eligibility, item_matches
from cafeops.services.loyalty.common import (
    audit,
    enqueue_wallet_update,
    now_utc,
    refresh_reward_available,
    touch,
)
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.scan import ScanView, scan_view
from cafeops.services.loyalty.staff_auth import StaffActor

__all__ = [
    "LOYALTY_RECEIPT_ID",
    "DrinkOption",
    "RedeemResult",
    "list_drinks",
    "redeem",
    "undo_redemption",
]

LOYALTY_RECEIPT_ID = "loyalty"


def _line_id(reward_id: int) -> str:
    return f"loyalty-reward-{reward_id}"


@dataclass(frozen=True, slots=True)
class RedeemResult:
    card: ScanView
    reward_id: int
    undo_until: datetime


def _choose_option(
    session: Session,
    card: LoyaltyCard,
    option_id: int | None,
    item: MenuItem | None,
) -> LoyaltyRewardOption | None:
    """Which catalogue entry this redemption is (phase 3).

    No catalogue: None, and the programme's own reward applies (phase 1). An explicit
    `option_id` must be an active entry of this card's programme. Without one, a single
    entry is taken, or -- for a phase-1 scanner that never sends one -- the first entry
    that allows the drink picked. Several entries and nothing to go on is a question for
    the till, not a guess.
    """
    from cafeops.services.loyalty.programs import item_facts, option_rule, reward_options

    options = reward_options(session, card.program_id)
    if option_id is not None:
        chosen = next((o for o in options if o.id == option_id), None)
        if chosen is None:
            raise LoyaltyError(
                404, "unknown_option", "That reward is not on this card's list any more."
            )
        return chosen
    if not options:
        return None
    if len(options) == 1:
        return options[0]
    if item is not None:
        facts = item_facts(session, [item.id])[item.id]
        for option in options:
            if item_matches(option_rule(option), facts):
                return option
    raise LoyaltyError(
        422,
        "option_required",
        "This card has several rewards to choose from: pick which one they are having.",
    )


def _check_item(
    session: Session, card: LoyaltyCard, option: LoyaltyRewardOption | None, item: MenuItem
) -> None:
    """The item must be one the reward covers, at or under its price cap."""
    from cafeops.services.loyalty.programs import item_facts, option_rule, program_rule

    program = card.program
    rule: Eligibility | None
    if option is not None:
        rule = option_rule(option)
        what = option.name
    else:
        # Phase 1 had no check here and the default programme keeps it that way; a
        # programme that says what earns (a matcha club) rewards the same things.
        rule = program_rule(program)
        what = program.reward_text
        if rule.is_default:
            rule = None
    if rule is not None:
        facts = item_facts(session, [item.id])[item.id]
        if not item_matches(rule, facts):
            raise LoyaltyError(
                409,
                "not_covered",
                f"{what} doesn't cover {item.name}: it covers {rule.describe()}.",
            )
    cap = program.reward_max_price_pence
    if option is not None and option.max_price_pence is not None:
        cap = option.max_price_pence
    if cap is not None and item.price_pence > cap:
        raise LoyaltyError(
            409,
            "over_price_cap",
            f"The reward covers up to £{cap / 100:.2f}; {item.name} is "
            f"£{item.price_pence / 100:.2f}.",
        )


def redeem(
    session: Session,
    actor: StaffActor,
    *,
    reward_id: int,
    menu_item_id: int | None,
    option_id: int | None = None,
) -> RedeemResult:
    now = now_utc()
    reward = session.get(LoyaltyReward, reward_id)
    if reward is None:
        raise LoyaltyError(404, "unknown_reward", "There is no reward with that number.")
    card = session.get(LoyaltyCard, reward.card_id)
    assert card is not None  # FK
    if card.voided_at is not None:
        raise LoyaltyError(409, "card_voided", "This card has been deleted.")
    if (
        reward.redeemed_at is not None
        or reward.voided_at is not None
        or (reward.expires_at is not None and reward.expires_at <= now)
    ):
        raise LoyaltyError(
            409, "reward_unavailable", "That reward has already been used or has expired."
        )

    sale_id: int | None = None
    item: MenuItem | None = None
    if menu_item_id is not None:
        item = session.get(MenuItem, menu_item_id)
        if item is None or not item.active:
            raise LoyaltyError(404, "unknown_drink", "That drink is not on the menu.")
    option = _choose_option(session, card, option_id, item)
    if item is not None:
        _check_item(session, card, option, item)
        sale = session.scalar(select(Sale).where(Sale.lightspeed_line_id == _line_id(reward.id)))
        if sale is None:
            sale = Sale(
                lightspeed_receipt_id=LOYALTY_RECEIPT_ID,
                lightspeed_line_id=_line_id(reward.id),
                menu_item_id=item.id,
                qty=Decimal("1"),
                gross_pence=0,
                sold_at=now,
                channel=SaleChannel.OTHER,
                applied_modifiers=[],
                voided=False,
                is_refund=False,
            )
            session.add(sale)
        else:
            # A redemption undone and made again. If the first one was already expanded
            # its movements stand and this re-point is recorded as a new sale time only;
            # inside the two-minute window that cannot happen (expansion is nightly).
            sale.menu_item_id = item.id
            sale.sold_at = now
            sale.voided = False
        session.flush()
        sale_id = sale.id

    reward.redeemed_at = now
    reward.redeemed_menu_item_id = menu_item_id
    reward.sale_id = sale_id
    reward.staff_user_id = actor.user_id
    reward.reward_option_id = option.id if option is not None else None
    audit(
        session,
        "redeem",
        f"reward {reward.id} ({reward.kind.value}"
        + (f", as {option.name}" if option is not None else "")
        + f") given by {actor.name} on {actor.device_name}",
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
        card_id=card.id,
        member_id=card.member_id,
        at=now,
    )
    refresh_reward_available(session, card, now)
    touch(card, now)
    card.member.last_activity_at = now
    enqueue_wallet_update(session, card.id, None)
    return RedeemResult(
        card=scan_view(session, card, now=now),
        reward_id=reward.id,
        undo_until=now + timedelta(seconds=UNDO_SECONDS),
    )


def undo_redemption(session: Session, actor: StaffActor, reward_id: int) -> ScanView:
    now = now_utc()
    reward = session.get(LoyaltyReward, reward_id)
    if reward is None or reward.redeemed_at is None:
        raise LoyaltyError(404, "unknown_reward", "That reward has not been given.")
    if now - reward.redeemed_at > timedelta(seconds=UNDO_SECONDS):
        raise LoyaltyError(
            409,
            "undo_expired",
            "Undo only works for two minutes. A manager can correct it from the back office.",
        )
    given_on = session.scalar(
        select(LoyaltyAudit.device_id)
        .where(LoyaltyAudit.kind == "redeem", LoyaltyAudit.card_id == reward.card_id)
        .order_by(LoyaltyAudit.at.desc())
    )
    if given_on != actor.device_id and not actor.is_manager:
        raise LoyaltyError(
            403, "not_allowed", "Only the till that gave it, or a manager, can undo this."
        )
    card = session.get(LoyaltyCard, reward.card_id)
    assert card is not None
    if reward.sale_id is not None:
        sale = session.get(Sale, reward.sale_id)
        if sale is not None:
            sale.voided = True
    reward.redeemed_at = None
    reward.redeemed_menu_item_id = None
    reward.sale_id = None
    reward.staff_user_id = None
    reward.reward_option_id = None
    audit(
        session,
        "redeem_undone",
        f"reward {reward.id} undone by {actor.name} on {actor.device_name}",
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
        card_id=card.id,
        member_id=card.member_id,
        at=now,
    )
    refresh_reward_available(session, card, now)
    touch(card, now)
    enqueue_wallet_update(session, card.id, None)
    return scan_view(session, card, now=now)


# --------------------------------------------------------------------------
# the drinks list
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrinkOption:
    menu_item_id: int
    name: str
    category: str | None
    price_pence: int | None


def drink_condition(now: datetime) -> ColumnElement[bool]:
    """The phase-1 "is this a drink" rule as a SQL condition on `MenuItem` (see below)."""
    cup_ingredients = select(Ingredient.id).where(
        Ingredient.category == "Packaging", Ingredient.name.ilike("%cup%")
    )
    uses_cup = exists().where(
        ManualRecipeLine.menu_item_id == MenuItem.id,
        ManualRecipeLine.ingredient_id.in_(cup_ingredients),
        ManualRecipeLine.effective_from <= now,
        or_(ManualRecipeLine.effective_to.is_(None), ManualRecipeLine.effective_to > now),
    )
    drink_categories = select(MenuCategory.name).where(MenuCategory.kind == MenuKind.DRINKS)
    return and_(
        or_(
            MenuItem.template_id.is_not(None),
            MenuItem.category.in_(drink_categories),
            uses_cup,
        ),
        ~MenuItem.name.ilike("%deal%"),
        ~MenuItem.name.ilike("%add-on%"),
        ~MenuItem.name.ilike("%upcharge%"),
    )


def item_label(item: MenuItem) -> str:
    size = item.size_code
    return item.name if size in (None, SizeCode.ONE) else f"{item.name} ({size.value})"


def list_drinks(session: Session) -> list[DrinkOption]:
    """Active menu items that are made-to-order drinks: what a free drink can be.

    `menu_item.category` is mostly empty on the live menu and `menu_category` has no rows,
    so a category filter alone would list almost nothing. A menu item counts as a drink
    when ANY of these holds:

    - it is built from a drink template (`template_id` set) -- templates are drinks by
      construction;
    - its category is a `menu_category` of kind DRINKS (for when the owner fills those in);
    - its current one-off recipe uses a paper cup (an ingredient in the Packaging category
      whose name contains "cup") -- every hot and cold drink the café hands over goes out
      in one, and no food does.

    Excluded: meal deals ("Deal" in the name -- they include a cup but are not a drink the
    card pays for) and add-ons ("add-on", "+upcharge"). Bottles and cans are not listed:
    the paper card stamps and rewards drinks the café makes, and SPEC's "any drink" is
    read as any of those. The owner can widen it by giving a category the DRINKS kind.

    One row per size, named "Latte (M)", because the £0 sale must deplete the right cup.
    """
    now = now_utc()
    rows = session.scalars(
        select(MenuItem)
        .where(MenuItem.active.is_(True), drink_condition(now))
        .order_by(MenuItem.name, MenuItem.price_pence)
    )
    out: list[DrinkOption] = []
    for item in rows:
        out.append(
            DrinkOption(
                menu_item_id=item.id,
                name=item_label(item),
                category=item.category,
                # 0 is "no price recorded", not "free": say unknown rather than £0.00.
                price_pence=item.price_pence or None,
            )
        )
    return out
