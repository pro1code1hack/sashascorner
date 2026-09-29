"""Pricing a basket on the server (CONTRACT §3.3, `POST /api/shop/quote`).

The client's totals are display only. Everything here is read live -- the size price
from `menu_item.price_pence`, the deltas from `shop_option.price_delta_pence` -- so an
order placed from a stale tab is refused (409 `price_changed`) rather than underpaid.

A quote never writes. It reports every problem it finds (an item sold out, an option
that no longer exists, a required choice missing) per line and overall, so the basket
page can point at the line instead of saying "something is wrong".

The reward (§3.5): with a card that holds an available free drink, `reward=True` takes
one unit off the priciest eligible line, capped by the programme's
`reward_max_price_pence`. Eligibility is the programme's own rule
(`services/loyalty/programs.program_rule`), the same one the till applies. A reward
already on another live order is not available to this one.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyReward,
    MenuItem,
    OrderStatus,
    ShopCategory,
    ShopOption,
    ShopOrder,
    ShopProduct,
    ShopProductOptionGroup,
)
from cafeops.domain.loyalty import item_matches
from cafeops.domain.shop import (
    LIVE_STATUSES,
    SIZE_LABELS,
    choose_reward_line,
    line_total,
    line_unit_price,
    selection_problem,
)
from cafeops.domain.units import pounds
from cafeops.services.loyalty.common import available_rewards
from cafeops.services.loyalty.programs import item_facts, program_rule
from cafeops.services.shop.catalog import groups_for_product, option_groups_by_id

__all__ = [
    "LineIn",
    "Quote",
    "QuotedLine",
    "QuotedOption",
    "RewardInfo",
    "available_reward_for",
    "quote",
]

MAX_LINES = 30
MAX_QTY = 20


@dataclass(frozen=True, slots=True)
class LineIn:
    product_id: int
    menu_item_id: int
    qty: int
    option_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class QuotedOption:
    group: str
    name: str
    price_delta_pence: int
    modifier_id: int | None
    #: The `shop_option.id`, kept on the order line so "order again" can rebuild it.
    option_id: int | None = None


@dataclass(frozen=True, slots=True)
class QuotedLine:
    product_id: int
    menu_item_id: int
    name: str
    size_label: str
    qty: int
    unit_price_pence: int
    line_total_pence: int
    options: tuple[QuotedOption, ...]
    problems: tuple[str, ...]
    #: The programme's rule says the free drink may be this.
    reward_eligible: bool
    #: The option ids actually applied (defaults filled in), for echoing back.
    option_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RewardInfo:
    applied: bool
    line_index: int | None
    text: str | None
    reward_id: int | None = None


@dataclass(frozen=True, slots=True)
class Quote:
    lines: tuple[QuotedLine, ...]
    subtotal_pence: int
    discount_pence: int
    total_pence: int
    reward: RewardInfo
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems and all(not ln.problems for ln in self.lines)


def available_reward_for(
    session: Session, card: LoyaltyCard, now: datetime
) -> LoyaltyReward | None:
    """The oldest available reward on the card that no live online order is holding."""
    held = set(
        session.scalars(
            select(ShopOrder.reward_id).where(
                ShopOrder.reward_id.is_not(None),
                ShopOrder.status.in_([OrderStatus(s) for s in LIVE_STATUSES]),
            )
        )
    )
    for reward in available_rewards(session, card.id, now):
        if reward.id not in held:
            return reward
    return None


def quote(
    session: Session,
    *,
    lines: Sequence[LineIn],
    reward: bool,
    card: LoyaltyCard | None,
    now: datetime | None = None,
) -> Quote:
    now = now or datetime.now(UTC)
    problems: list[str] = []
    if not lines:
        problems.append("The basket is empty.")
    if len(lines) > MAX_LINES:
        problems.append(f"A basket holds at most {MAX_LINES} lines.")

    products = {
        p.id: p
        for p in session.scalars(
            select(ShopProduct).where(ShopProduct.id.in_({ln.product_id for ln in lines}))
        )
    }
    items = {
        i.id: i
        for i in session.scalars(
            select(MenuItem).where(MenuItem.id.in_({ln.menu_item_id for ln in lines}))
        )
    }
    slug_by_ops = {c.ops_name: c.slug for c in session.scalars(select(ShopCategory))}
    groups = option_groups_by_id(session)
    options_by_id: dict[int, ShopOption] = {o.id: o for g in groups.values() for o in g.options}
    attachments: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for link in session.scalars(select(ShopProductOptionGroup)):
        attachments[link.product_id].append((link.group_id, link.sort_order))

    rule = program_rule(card.program) if card is not None else None
    facts = item_facts(session, items.keys()) if rule is not None else {}

    quoted: list[QuotedLine] = []
    for ln in lines[:MAX_LINES]:
        line_problems: list[str] = []
        product = products.get(ln.product_id)
        item = items.get(ln.menu_item_id)
        name = product.display_name or product.item_name if product else f"item {ln.product_id}"
        if product is None or not product.visible:
            line_problems.append(f"{name} is not on the online menu.")
        elif not product.available:
            line_problems.append(f"{name} is sold out today.")
        if (
            item is None
            or not item.active
            or (product is not None and item.name != product.item_name)
        ):
            line_problems.append(f"{name}: that size is not available.")
        if not 1 <= ln.qty <= MAX_QTY:
            line_problems.append(f"{name}: quantity must be 1 to {MAX_QTY}.")
        size_code = item.size_code.value if item is not None and item.size_code else "ONE"
        size_label = SIZE_LABELS.get(size_code, "")

        chosen_options: list[QuotedOption] = []
        applied_ids: list[int] = []
        if product is not None and item is not None:
            category_slug = slug_by_ops.get(product.category_ops_name or "")
            effective = groups_for_product(product, category_slug, groups, attachments)
            allowed = {o.id: g for g in effective for o in g.options}
            by_group: dict[int, list[ShopOption]] = defaultdict(list)
            for oid in dict.fromkeys(ln.option_ids):
                opt = options_by_id.get(oid)
                if opt is None or oid not in allowed:
                    line_problems.append(f"{name}: an option you chose is no longer offered.")
                    continue
                if not opt.available:
                    line_problems.append(f"{name}: {opt.name} is not available today.")
                    continue
                by_group[opt.group_id].append(opt)
            for group in effective:
                picks = by_group.get(group.id, [])
                if not picks and (group.required or group.min_select >= 1):
                    default = next((o for o in group.options if o.is_default and o.available), None)
                    if default is not None:
                        picks = [default]
                problem = selection_problem(
                    group_name=group.name,
                    kind=group.kind.value,
                    required=group.required,
                    min_select=group.min_select,
                    max_select=group.max_select,
                    chosen=len(picks),
                )
                if problem is not None:
                    line_problems.append(f"{name}: {problem}")
                for opt in picks:
                    chosen_options.append(
                        QuotedOption(
                            group=group.name,
                            name=opt.name,
                            price_delta_pence=opt.price_delta_pence,
                            modifier_id=opt.modifier_id,
                            option_id=opt.id,
                        )
                    )
                    applied_ids.append(opt.id)

        base = item.price_pence if item is not None else 0
        unit = line_unit_price(base, (o.price_delta_pence for o in chosen_options))
        qty = ln.qty if 1 <= ln.qty <= MAX_QTY else 1
        eligible = (
            rule is not None
            and item is not None
            and item.id in facts
            and item_matches(rule, facts[item.id])
        )
        quoted.append(
            QuotedLine(
                product_id=ln.product_id,
                menu_item_id=ln.menu_item_id,
                name=name,
                size_label=size_label,
                qty=qty,
                unit_price_pence=unit,
                line_total_pence=line_total(unit, qty),
                options=tuple(chosen_options),
                problems=tuple(line_problems),
                reward_eligible=eligible,
                option_ids=tuple(applied_ids),
            )
        )

    subtotal = sum(ln.line_total_pence for ln in quoted)
    reward_info = RewardInfo(False, None, None)
    discount = 0
    if reward:
        if card is None:
            reward_info = RewardInfo(
                False, None, "Sign in to your Rewards card to use a free drink."
            )
        elif card.voided_at is not None:
            reward_info = RewardInfo(False, None, "This card has been deleted.")
        else:
            held = available_reward_for(session, card, now)
            if held is None:
                reward_info = RewardInfo(False, None, "No free drink on your card yet.")
            else:
                pick = choose_reward_line(
                    [(ln.unit_price_pence, ln.reward_eligible) for ln in quoted],
                    cap_pence=card.program.reward_max_price_pence,
                )
                if pick.line_index is None:
                    reward_info = RewardInfo(False, None, pick.reason)
                else:
                    discount = pick.discount_pence
                    reward_info = RewardInfo(
                        True,
                        pick.line_index,
                        f"{card.program.reward_text}: -{pounds(discount)} on "
                        f"{quoted[pick.line_index].name}",
                        held.id,
                    )
    return Quote(
        lines=tuple(quoted),
        subtotal_pence=subtotal,
        discount_pence=discount,
        total_pence=max(0, subtotal - discount),
        reward=reward_info,
        problems=tuple(problems),
    )
