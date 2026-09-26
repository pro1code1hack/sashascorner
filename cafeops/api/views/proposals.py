"""Import review: the 27 proposed templates, and confirming one.

Spec 6 is explicit that pattern detection **proposes, never writes**, and that a
human confirms in the UI before any template exists. The CLI half has always been
there (`cafeops proposals`, `cafeops materialise-template`); this is the half the
spec actually asked for, and the last screen in the spec 10 build order.

Two things this view is careful about.

**A conflict is the point, not an error.** 27 proposals cover 314 legacy rows, and
where two items in the same group disagree about a quantity the detector records
the disagreement rather than averaging it away. Those proposals are refused by
default. Confirming one with `allow_conflicts` takes the LOWEST quantity at each
size, which is arbitrary by construction -- so the report says so, in a warning
that names the actor, and the resulting quantities need checking against the real
recipes. The screen must not present that as a clean success.

**Singletons are excluded, not hidden.** 43 one-off groups are not patterns; the
right model for them is a manual recipe, which is what they already are. They are
counted in the response so the screen can say "27 of 70 groups are patterns"
rather than silently listing a subset.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.schemas import (
    MaterialiseIn,
    MaterialiseResponse,
    ProposalAxisOut,
    ProposalComponentOut,
    ProposalConflictOut,
    ProposalOut,
    ProposalsResponse,
)
from cafeops.db.models.composition import DrinkTemplate
from cafeops.seed.patterns import TemplateProposal
from cafeops.services.materialise_template import (
    AmbiguousProposal,
    ProposalAlreadyMaterialised,
    ProposalHasConflicts,
    ProposalNotFound,
    list_proposals,
    materialise_proposal,
)

__all__ = ["materialise_proposal_view", "proposals_view"]


def _existing_template_names(session: Session) -> set[str]:
    return set(session.scalars(select(DrinkTemplate.name)))


def _proposal_out(
    p: TemplateProposal, *, existing: set[str], shared_names: set[str]
) -> ProposalOut:
    already = p.name in existing
    blocked: str | None = None
    if p.is_hollow:
        blocked = (
            f"no components and no axes -- there is no recipe in this group to write. "
            f"Confirming would create an empty template and take its {p.menu_item_count} "
            "menu item(s) off the manual recipes they resolve through today."
        )
    elif already:
        blocked = (
            f"a template named {p.name!r} already exists -- edit its components rather "
            "than materialising the proposal a second time"
        )
    elif p.conflicts:
        blocked = (
            f"{len(p.conflicts)} unresolved conflict(s): the legacy rows disagree about a "
            "quantity, so a human must say which is right. Confirming anyway takes the "
            "lowest quantity at each size and records that the choice was made."
        )
    return ProposalOut(
        proposal_id=p.proposal_id,
        name=p.name,
        category=p.category,
        menu_item_count=p.menu_item_count,
        base_item_names=tuple(p.base_item_names),
        sizes=tuple(p.sizes),
        components=tuple(
            ProposalComponentOut(
                role=c.role.value,
                ingredient_name=c.ingredient_name,
                qty_by_size=dict(c.qty_by_size),
            )
            for c in p.components
        ),
        axes=tuple(
            ProposalAxisOut(
                name=a.name,
                role=a.role.value,
                options=dict(a.options),
                option_count=a.option_count,
            )
            for a in p.axes
        ),
        conflicts=tuple(
            ProposalConflictOut(
                role=c.role.value,
                ingredient_name=c.ingredient_name,
                size_code=c.size_code,
                quantities=dict(c.quantities),
                describe=c.describe(),
            )
            for c in p.conflicts
        ),
        is_hollow=p.is_hollow,
        name_is_ambiguous=p.name in shared_names,
        already_materialised=already,
        blocked_reason=blocked,
    )


def proposals_view(session: Session) -> ProposalsResponse:
    """Every proposed template waiting for a human. Writes nothing."""
    found = list_proposals(session)
    singletons = sum(1 for p in found if p.is_singleton)
    patterns = [p for p in found if not p.is_singleton]
    existing = _existing_template_names(session)
    seen: dict[str, int] = {}
    for p in patterns:
        seen[p.name] = seen.get(p.name, 0) + 1
    shared_names = {n for n, c in seen.items() if c > 1}
    out = tuple(
        _proposal_out(p, existing=existing, shared_names=shared_names)
        for p in sorted(patterns, key=lambda p: (-p.menu_item_count, p.name))
    )
    return ProposalsResponse(
        proposals=out,
        total=len(out),
        with_conflicts=sum(1 for p in out if p.conflicts),
        singletons_excluded=singletons,
        writes_nothing=True,
    )


def materialise_proposal_view(
    session: Session, *, key: str, body: MaterialiseIn
) -> MaterialiseResponse:
    """Confirm one proposal into real composition rows.

    `materialise_proposal` commits its own transaction, so this must not be
    wrapped in an outer commit that could roll part of it back -- the same
    arrangement as `apply_edit_view`.
    """
    try:
        report = materialise_proposal(
            session,
            key,
            actor=body.actor,
            effective_from=datetime.now(UTC),
            allow_conflicts=body.allow_conflicts,
        )
    except AmbiguousProposal as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc)},
        ) from None
    except ProposalNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": f"no proposal matching {key!r}"},
        ) from None
    except ProposalAlreadyMaterialised as exc:
        # 409: the proposal is fine, the world moved. Same shape as a superseded
        # composition edit, so the screen can reuse that handling.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc)},
        ) from None
    except ProposalHasConflicts as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc)},
        ) from None

    return MaterialiseResponse(
        proposal_name=report.proposal_name,
        template_id=report.template_id,
        sizes=tuple(report.sizes),
        components=report.components,
        axis_filled_slots=report.axis_filled_slots,
        axes=report.axes,
        options=report.options,
        items_repointed=report.items_repointed,
        manual_lines_closed=report.manual_lines_closed,
        accepted_conflicts=report.accepted_conflicts,
        items_unresolved_option=tuple(report.items_unresolved_option),
        items_skipped_other_template=tuple(report.items_skipped_other_template),
        items_not_found=tuple(report.items_not_found),
        missing_ingredients=tuple(report.missing_ingredients),
        warnings=tuple(report.warnings),
        summary=report.summary(),
    )
