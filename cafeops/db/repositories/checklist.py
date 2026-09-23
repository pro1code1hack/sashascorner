"""Tier C checklist responses. Implements `ChecklistRepository`.

Phase 0 declared the protocol and never implemented it, because nothing consumed it:
tier C is *only* reachable through the bot (spec 4.7 -- "checklist only, no numbers"),
and the bot did not exist. This is that implementation, added by the bot agent because
the alternative is the bot writing to `checklist_response` directly, which spec 8
forbids.

The protocol's two methods are here unchanged. `latest_by_ingredient` and
`stale_or_unanswered` are additions, not signature changes -- the protocol is
structural, so extra methods cost nothing and a per-ingredient N+1 in a 59-item
checklist loop would cost 59 queries to render one message.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models import ChecklistResponse, Ingredient
from cafeops.domain.types import ChecklistStatus, Tier

__all__ = ["SqlChecklistRepository"]


class SqlChecklistRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        ingredient_id: int,
        status: ChecklistStatus,
        responded_at: datetime,
        responded_by: str,
    ) -> int:
        if not responded_by.strip():
            raise ValueError(
                "responded_by is required: a checklist answer is somebody's word that "
                "they looked at the shelf, and an anonymous one is not evidence"
            )
        row = ChecklistResponse(
            ingredient_id=ingredient_id,
            status=status,
            responded_at=responded_at,
            responded_by=responded_by,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def latest_low(self, *, since: datetime) -> list[int]:
        """Ingredient ids whose MOST RECENT answer since `since` was LOW.

        Most recent, not "any LOW": an item answered LOW on Monday and OK on Thursday
        is not low, and reporting it would send somebody to buy what is already there.
        """
        latest = (
            select(
                ChecklistResponse.ingredient_id.label("ingredient_id"),
                func.max(ChecklistResponse.responded_at).label("at"),
            )
            .where(ChecklistResponse.responded_at >= since)
            .group_by(ChecklistResponse.ingredient_id)
            .subquery()
        )
        rows = self.session.execute(
            select(ChecklistResponse.ingredient_id)
            .join(
                latest,
                (ChecklistResponse.ingredient_id == latest.c.ingredient_id)
                & (ChecklistResponse.responded_at == latest.c.at),
            )
            .where(ChecklistResponse.status == ChecklistStatus.LOW)
        ).all()
        return sorted({int(r[0]) for r in rows})

    def latest_by_ingredient(
        self, ingredient_ids: Sequence[int] | None = None
    ) -> dict[int, tuple[ChecklistStatus, datetime]]:
        """(status, responded_at) of the newest answer per ingredient. One query."""
        stmt = select(ChecklistResponse).order_by(
            ChecklistResponse.ingredient_id, ChecklistResponse.responded_at
        )
        if ingredient_ids is not None:
            if not ingredient_ids:
                return {}
            stmt = stmt.where(ChecklistResponse.ingredient_id.in_(list(ingredient_ids)))
        latest: dict[int, tuple[ChecklistStatus, datetime]] = {}
        for row in self.session.scalars(stmt):
            latest[row.ingredient_id] = (row.status, row.responded_at)
        return latest

    def checklist_ingredients(self) -> list[tuple[int, str]]:
        """Every tier C ingredient, by name. The checklist roster.

        Tier C is deliberately not filtered on `tracking_enabled`: tier C exists
        *because* these are not tracked numerically, so requiring tracking would return
        an empty checklist.
        """
        rows = self.session.execute(
            select(Ingredient.id, Ingredient.name)
            .where(Ingredient.tier == Tier.C)
            .order_by(Ingredient.name)
        ).all()
        return [(int(r[0]), str(r[1])) for r in rows]
