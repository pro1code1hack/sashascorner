"""The words on the pass, shared by Apple and Google so the two never say different things.

Everything is derived from `CardView`; nothing here reads the database.

Phase 3: a card belongs to a programme. The main stamp card reads exactly as before; a
club card ("Matcha club") names itself, and a POINTS card counts points. The strip is
always the sticker strip (`assets/pass/stickers/`): a stamp card shows one sticker per
stamp; a points card, whose target (100) is far past the 20 slots a strip holds, shows
its progress as 8 slots -- the same stickers filling up as the points do.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from cafeops.config import settings

if TYPE_CHECKING:
    from cafeops.services.loyalty.card_view import CardView

WELCOME = "Welcome to Sasha's Corner Rewards"
DELETED = "Card deleted"


#: Slots on a points card's strip (the paper card's eight).
POINT_SLOTS = 8
MAX_STRIP_SLOTS = 20


def is_points(view: CardView) -> bool:
    return view.program_kind == "POINTS"


def is_main(view: CardView) -> bool:
    return view.program_slug == "stamp"


def unit_label(view: CardView) -> str:
    """ "Stamps" or "Points": the header label on both passes."""
    return "Points" if is_points(view) else "Stamps"


def strip_counts(view: CardView) -> tuple[int, int]:
    """(filled, slots) for the sticker strip."""
    required = max(1, view.stamps_required)
    current = max(0, view.stamps_current)
    if not is_points(view) and required <= MAX_STRIP_SLOTS:
        return min(current, required), required
    filled = min(POINT_SLOTS, current * POINT_SLOTS // required)
    return filled, POINT_SLOTS


def strip_stickers(view: CardView) -> tuple[str, ...] | None:
    """The card's own sticker per filled slot (BACKOFFICE-V2 §2); None on a points card,
    whose strip is a progress bar of the fixed art."""
    if is_points(view) or not view.stickers:
        return None
    filled, _ = strip_counts(view)
    return tuple(view.stickers[:filled])


def stamps_to_go(view: CardView) -> int:
    return max(0, view.stamps_required - view.stamps_current)


def stamp_reward_ready(view: CardView) -> bool:
    """A stamp-card drink is waiting: the strip shows the reward sticker."""
    return not view.voided and any(r.kind == "STAMP_CARD" for r in view.rewards)


def headline(view: CardView) -> str:
    """The one line that matters: what the customer can have, or how far off it is.

    A ready stamp-card drink outranks a birthday one because it never expires; both are
    listed in `reward_lines` anyway.
    """
    if view.voided:
        return DELETED
    kinds = {r.kind for r in view.rewards}
    if "STAMP_CARD" in kinds:
        return view.reward_ready_label or "Free drink ready"
    if view.rewards:
        return view.rewards[0].label
    n = stamps_to_go(view)
    # "Free matcha ready" -> "Free matcha after 3 more"; the main card's label gives the
    # phase-1 line exactly ("Free drink after 3 more").
    label = view.reward_ready_label or "Free drink ready"
    thing = label[: -len(" ready")] if label.lower().endswith(" ready") else "Reward"
    if is_points(view):
        return f"{thing} after {n} more point{'' if n == 1 else 's'}"
    return f"{thing} after {n} more"


def stamps_value(view: CardView) -> str:
    return f"{view.stamps_current}/{view.stamps_required}"


def _expiry(at: datetime | None) -> str:
    if at is None:
        return ""
    # Stored UTC; a birthday voucher ending at 00:30 BST must read as that local day.
    local = at.astimezone(settings.tz)
    return f" (until {local.day} {local:%b})"


def reward_lines(view: CardView) -> list[str]:
    return [f"{r.label}{_expiry(r.expires_at)}" for r in view.rewards]


def rules_text(view: CardView) -> str:
    n = view.stamps_required
    if is_points(view):
        return (
            f"{view.points_per_pound or 0} points for every £1 you spend. At {n} points your "
            f"reward is ready: {view.reward_text}. Points above {n} carry over, and a reward "
            "doesn't expire while your card is active. Show this card at the till before you "
            "pay. One card per person."
        )
    if not is_main(view):
        intro = f"{view.program_description} " if view.program_description else ""
        return (
            f"{intro}One stamp for each qualifying drink, up to 3 per visit. Collect {n} "
            f"stamps and your reward is ready: {view.reward_text}. Stamps above {n} carry "
            "over. Show this card at the till before you pay. One card per person."
        )
    return (
        f"One stamp for every hot or cold drink, up to 3 per visit. Collect {n} stamps and "
        f"your reward is ready: {view.reward_text}. Stamps above {n} carry over, and a "
        "stamp-card reward doesn't expire while your card is active. Show this card at the "
        "till before you pay. One card per person."
    )


__all__ = [
    "DELETED",
    "POINT_SLOTS",
    "WELCOME",
    "headline",
    "is_main",
    "is_points",
    "reward_lines",
    "rules_text",
    "stamp_reward_ready",
    "stamps_to_go",
    "stamps_value",
    "strip_counts",
    "strip_stickers",
    "unit_label",
]
