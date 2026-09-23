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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from cafeops.db.repositories.checklist import SqlChecklistRepository
from cafeops.domain.types import ChecklistStatus

__all__ = ["ChecklistAnswer", "ChecklistRoster", "checklist_roster", "record_checklist_answer"]

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
        return tuple(
            i for i, (status, _) in self.latest.items() if status is ChecklistStatus.LOW
        )


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
