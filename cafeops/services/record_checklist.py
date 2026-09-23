"""Record a tier C checklist answer. Spec 4.7: yes/no, never a number.

A service rather than a repository call from the handler, because spec 8 says the bot,
the API and the agent are all clients of the services layer and none of them touches
the database. This one is thin on purpose -- there is no arithmetic to do. What it adds
is the two refusals:

* **A named human.** `responded_by` is somebody's word that they looked at the shelf.
* **No quantity.** Tier C has no par level and no forecast, so a number entered here
  would look like stock data and be treated as such by nothing. The signature makes
  that unrepresentable rather than merely discouraged.

`LOW` is not an order. It is an item for the next order's human review, which is what
tier C means (spec 4.7) -- and the reason this writes no `stock_movement`: nobody
counted anything.

## What a `LOW` answer actually DOES -- `request_checklist_order`

It used to do nothing. The digest listed every tier C item whose last answer was
«заканчивается» and there the trail ended: no par level, no forecast, nothing that could
size an order, so fifty-nine items were walked every week to produce a list nobody could
act on without retyping it somewhere else. A checklist nobody acts on is a checklist
people stop filling in, and then the one signal tier C has is gone too.

`request_checklist_order` is the smallest honest thing that can follow a `LOW`:

* **A person names the quantity.** Spec 4.7 says tier C is never calculated, so the
  system has no number and must not invent one. It takes `packs` from whoever answered.
  There is deliberately no default, no "last time you ordered N", and no par floor --
  every one of those would be a figure the system does not have, dressed as one it does.
* **It lands on a DRAFT, never an order.** The line joins the supplier's existing draft
  if there is one, and opens a `DRAFT` if not. Invariant 1 is untouched: she still
  confirms it, with the line in front of her.
* **The line says where it came from.** `po_line.checklist_requested_by` carries the
  answerer's name, so every surface can mark it as a checklist request rather than a
  forecast. A quantity somebody guessed and a quantity the system computed must never
  look the same on a card she is about to approve.
* **It refuses rather than guesses.** No supplier product for the ingredient means no
  price and no pack size, so there is no honest line to write -- and the refusal says so
  instead of inventing a pack of one at zero pence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.repositories.checklist import SqlChecklistRepository
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.db.repositories.sourcing import SqlSourcingRepository
from cafeops.db.repositories.supplier import SqlSupplierRepository
from cafeops.domain.ordering import target_delivery_date
from cafeops.domain.types import ChecklistStatus, Tier, Unit

__all__ = [
    "ChecklistAnswer",
    "ChecklistOrderRequest",
    "ChecklistRequestRefused",
    "ChecklistRoster",
    "checklist_roster",
    "record_checklist_answer",
    "request_checklist_order",
]


class ChecklistRequestRefused(ValueError):
    """The request was not written, and the message says what is missing.

    A refusal rather than a line with invented terms: an order line needs a supplier, a
    pack size and a price, and tier C has no forecast to fall back on if any of them is
    guessed wrong.
    """


#: An answer older than this is treated as stale and re-asked. A week: tier C is the
#: cake-and-sundries list, and asking daily is how a checklist stops being answered.
DEFAULT_STALE_DAYS = 7


@dataclass(frozen=True, slots=True)
class ChecklistAnswer:
    response_id: int
    ingredient_id: int
    ingredient_name: str
    status: ChecklistStatus
    responded_at: datetime
    responded_by: str


@dataclass(frozen=True, slots=True)
class ChecklistRoster:
    """The tier C list plus each item's last answer, if any."""

    items: tuple[tuple[int, str], ...]
    latest: dict[int, tuple[ChecklistStatus, datetime]]
    stale_cutoff: datetime

    def is_stale(self, ingredient_id: int) -> bool:
        seen = self.latest.get(ingredient_id)
        return seen is None or seen[1] < self.stale_cutoff

    @property
    def low_ids(self) -> tuple[int, ...]:
        return tuple(i for i, (status, _) in self.latest.items() if status is ChecklistStatus.LOW)


def record_checklist_answer(
    session: Session,
    *,
    ingredient_id: int,
    status: ChecklistStatus,
    responded_by: str,
    responded_at: datetime | None = None,
) -> ChecklistAnswer:
    """Write one tier C answer. Returns it back, named, for the confirmation message."""
    from cafeops.db.models import Ingredient

    if not responded_by.strip():
        raise ValueError(
            "responded_by is required: an anonymous checklist answer is not evidence "
            "that anybody looked at the shelf"
        )
    responded_at = responded_at or datetime.now(UTC)
    if responded_at.tzinfo is None:
        raise ValueError("responded_at must be timezone-aware (spec 4: all timestamps UTC)")

    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")

    repo = SqlChecklistRepository(session)
    response_id = repo.record(ingredient_id, status, responded_at, responded_by)
    return ChecklistAnswer(
        response_id=response_id,
        ingredient_id=ingredient_id,
        ingredient_name=ingredient.name,
        status=status,
        responded_at=responded_at,
        responded_by=responded_by,
    )


def checklist_roster(
    session: Session, *, at: datetime | None = None, stale_days: int = DEFAULT_STALE_DAYS
) -> ChecklistRoster:
    """The tier C roster and the freshness of each answer."""
    at = at or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware (spec 4: all timestamps UTC)")
    repo = SqlChecklistRepository(session)
    items = tuple(repo.checklist_ingredients())
    return ChecklistRoster(
        items=items,
        latest=repo.latest_by_ingredient([i for i, _ in items]),
        stale_cutoff=at - timedelta(days=stale_days),
    )


@dataclass(frozen=True, slots=True)
class ChecklistOrderRequest:
    """One tier C item put on a draft order at a quantity a person chose."""

    po_id: int
    po_line_id: int
    #: True when this opened a new DRAFT rather than joining one already waiting.
    order_created: bool
    ingredient_id: int
    ingredient_name: str
    supplier_id: int
    supplier_name: str
    packs: int
    pack_size: Decimal
    pack_unit: Unit
    unit_price_pence: int
    target_delivery_date: date
    requested_by: str
    #: Six of eight suppliers' terms are invented (`ARCHITECTURE.md` 8F.4), and the
    #: delivery date above rests on them. Carried so the message can say so.
    terms_are_placeholders: bool

    @property
    def line_total_pence(self) -> int:
        return self.packs * self.unit_price_pence


def request_checklist_order(
    session: Session,
    *,
    ingredient_id: int,
    packs: int,
    requested_by: str,
    at: datetime | None = None,
) -> ChecklistOrderRequest:
    """Put a tier C item on its supplier's DRAFT order at a HUMAN-CHOSEN quantity.

    See the module docstring for why this is the shape it is. Four refusals, and each one
    would otherwise be a number the system does not have:

    1. Not tier C -- tier A and B are forecast, and a hand-typed quantity there would
       compete with the gate and the cover window rather than fill a gap in them.
    2. `packs <= 0` -- "do not order" is expressed by not asking (checked in the
       repository, which is also where a duplicate request is merged).
    3. No supplier product -- no pack size, no price, no honest line.
    4. No name -- the quantity is somebody's decision, so the line has to say whose.
    """
    from cafeops.db.models import Ingredient

    at = at or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware (spec 4: all timestamps UTC)")
    if not requested_by.strip():
        raise ChecklistRequestRefused(
            "requested_by is required: tier C carries no forecast, so this quantity is "
            "somebody's decision and the line has to say whose"
        )

    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.tier is not Tier.C:
        raise ChecklistRequestRefused(
            f"{ingredient.name} is tier {ingredient.tier.value}, not tier C. A tier A or B "
            "quantity comes from the forecast and the cover window (spec 5.4); typing one "
            "here would put a guess next to a calculation and make them indistinguishable."
        )

    sourcing = SqlSourcingRepository(session)
    options = sourcing.options_for(ingredient_id)
    if not options:
        raise ChecklistRequestRefused(
            f"{ingredient.name} has no supplier product on file, so there is no pack size "
            "and no price to put on a line. Refusing rather than inventing a pack: a tier C "
            "item has no forecast to correct the guess against."
        )
    option = options[0]

    terms = sourcing.terms(option.supplier_id)
    supplier = SqlSupplierRepository(session).get(option.supplier_id)
    if terms is None or supplier is None:  # pragma: no cover - FK guarantees both
        raise ChecklistRequestRefused(
            f"supplier {option.supplier_id} has no terms on file, so no delivery date can "
            "be computed for this line"
        )

    # The supplier's own next slot, not "today plus a guess". `cutoff_time` is honoured
    # for the same reason the ordering path honours it: a request typed after the cutoff
    # arrives a day later, and pretending otherwise is how the shelf runs out on a
    # promise. LOCAL date and time, because a cutoff is a fact about the supplier's
    # warehouse day rather than about UTC (`domain/ordering.cutoff_slip_days`).
    local = at.astimezone(settings.tz)
    target = target_delivery_date(
        order_date=local.date(),
        supplier=supplier,
        order_time=local.time(),
        cutoff_time=terms.cutoff_time,
    )

    po_id, line_id, created, delivery_on = SqlPurchaseOrderRepository(session).add_checklist_line(
        ingredient_id=ingredient_id,
        supplier_id=option.supplier_id,
        supplier_product_id=option.supplier_product_id,
        packs=packs,
        unit_price_pence=option.price_pence,
        requested_by=requested_by,
        target_delivery_date=target,
    )
    return ChecklistOrderRequest(
        po_id=po_id,
        po_line_id=line_id,
        order_created=created,
        ingredient_id=ingredient_id,
        ingredient_name=ingredient.name,
        supplier_id=option.supplier_id,
        supplier_name=terms.name,
        packs=packs,
        pack_size=option.pack_size,
        pack_unit=option.pack_unit,
        unit_price_pence=option.price_pence,
        # The order's OWN date, which is the earliest slot it can make -- not the date
        # this request was computed against. A joined draft may already be later.
        target_delivery_date=delivery_on,
        requested_by=requested_by,
        terms_are_placeholders=terms.terms_are_placeholders,
    )
