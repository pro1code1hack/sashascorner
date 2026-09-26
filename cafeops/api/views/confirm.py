"""The two confirmations the dashboard can write: supplier terms, and a shelf life.

These are the API half of `cafeops supplier confirm` and `cafeops shelf-life set`.
Both exist because `cafeops doctor`'s two standing warnings -- six suppliers on
invented terms, a hundred shelf lives on seeded guesses -- had no operator path at
all until now; the remedy on offer was to edit a seed file in the repository.

Every refusal lives in `services/confirm_terms.py`, not here. This module's whole
job is to turn a `ConfirmationRefused` into a 422 whose body says what to fix, and
to report the CONSEQUENCE of the write rather than just acknowledging it:

  - a supplier confirmation returns which terms actually moved, so the screen can
    show that confirming Booker changed the minimum but not the lead time;
  - a shelf-life confirmation returns `usable_days_changed_by`, because that is
    the number that matters. Shelf life caps order size (invariant 4), so the
    honest answer to "what did I just do" is "a single order may now cover three
    more days of trade", not "saved".

Neither is an order and neither spends anything, so invariant 1 is untouched.
"""

from __future__ import annotations

from datetime import time

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.schemas import (
    ShelfLifeIn,
    ShelfLifeResponse,
    SupplierTermsIn,
    SupplierTermsResponse,
)
from cafeops.api.views.orders import _supplier_out
from cafeops.db.models.enums import PriceSource
from cafeops.db.models.supplier import Supplier
from cafeops.db.repositories.sourcing import SqlSourcingRepository
from cafeops.services.confirm_terms import (
    ConfirmationRefused,
    SupplierTerms,
    confirm_shelf_life,
    confirm_supplier_terms,
)

__all__ = ["confirm_shelf_life_view", "confirm_supplier_terms_view"]

_SOURCES = {
    "supplier": PriceSource.SUPPLIER_FEED,
    "packaging": PriceSource.INVOICE,
    "invoice": PriceSource.INVOICE,
}


def _refuse(exc: ConfirmationRefused) -> HTTPException:
    """A refusal is a 422 with the sentence the service wrote, not a generic 400.

    The messages are written to be shown to the person who typed the number --
    "a 1-day life against a 2-day transit buffer leaves nothing usable on arrival"
    is the whole explanation -- so they pass through unaltered.
    """
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"message": str(exc)},
    )


def _parse_cutoff(raw: str | None) -> time | None:
    if raw is None or raw == "":
        return None
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": f"cutoff_time must be HH:MM. Got {raw!r}."},
        ) from None


def _describe(label: str, before: object, after: object) -> str | None:
    return None if before == after else f"{label}: {before} -> {after}"


def confirm_supplier_terms_view(
    session: Session, *, supplier_id: int, body: SupplierTermsIn
) -> SupplierTermsResponse:
    """Record terms confirmed with a supplier and clear the invented-terms flag."""
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": f"no supplier with id {supplier_id}"},
        )

    try:
        result = confirm_supplier_terms(
            session,
            name=supplier.name,
            terms=SupplierTerms(
                lead_time_days=body.lead_time_days,
                delivery_weekdays=tuple(body.delivery_weekdays),
                min_order_pence=body.min_order_pence,
                delivery_fee_pence=body.delivery_fee_pence,
                cutoff_time=_parse_cutoff(body.cutoff_time),
                free_delivery_threshold_pence=body.free_delivery_threshold_pence,
            ),
        )
    except ConfirmationRefused as exc:
        raise _refuse(exc) from None

    b, a = result.before, result.after
    changed = tuple(
        c
        for c in (
            _describe("lead time", f"{b.lead_time_days}d", f"{a.lead_time_days}d"),
            _describe(
                "delivery days",
                ",".join(str(d) for d in b.delivery_weekdays) or "none",
                ",".join(str(d) for d in a.delivery_weekdays) or "none",
            ),
            _describe("minimum order", b.min_order_pence, a.min_order_pence),
            _describe("delivery fee", b.delivery_fee_pence, a.delivery_fee_pence),
            _describe(
                "cutoff",
                b.cutoff_time.isoformat() if b.cutoff_time else "none",
                a.cutoff_time.isoformat() if a.cutoff_time else "none",
            ),
            _describe(
                "free-delivery threshold",
                b.free_delivery_threshold_pence,
                a.free_delivery_threshold_pence,
            ),
        )
        if c is not None
    )

    session.flush()
    terms = next(
        t for t in SqlSourcingRepository(session).all_terms() if t.supplier_id == result.supplier_id
    )
    return SupplierTermsResponse(
        supplier_id=result.supplier_id,
        name=result.name,
        was_placeholder=result.was_placeholder,
        changed=changed,
        supplier=_supplier_out(terms),
    )


def confirm_shelf_life_view(
    session: Session, *, ingredient_id: int, body: ShelfLifeIn
) -> ShelfLifeResponse:
    """Record a checked shelf life. Changes order size from the next run."""
    from cafeops.db.models.ingredient import Ingredient

    ing = session.scalars(select(Ingredient).where(Ingredient.id == ingredient_id)).one_or_none()
    if ing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": f"no ingredient with id {ingredient_id}"},
        )

    source = _SOURCES.get(body.source.lower())
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    f"source must be 'supplier' or 'packaging'. Got {body.source!r}. "
                    "'estimate' is what this call is leaving behind, so it is not accepted."
                )
            },
        )

    try:
        r = confirm_shelf_life(
            session,
            name=ing.name,
            shelf_life_days=body.shelf_life_days,
            open_life_days=body.open_life_days,
            source=source,
        )
    except ConfirmationRefused as exc:
        raise _refuse(exc) from None

    return ShelfLifeResponse(
        ingredient_id=r.ingredient_id,
        name=r.name,
        shelf_life_days_before=r.shelf_life_days_before,
        shelf_life_days_after=r.shelf_life_days_after,
        open_life_days_after=r.open_life_days_after,
        transit_buffer_days=r.transit_buffer_days,
        source_before=r.source_before.value if r.source_before else None,
        source_after=r.source_after.value,
        usable_days_after=r.usable_days_after,
        usable_days_changed_by=r.usable_days_changed_by,
    )
