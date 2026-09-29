"""The Setup checklist: five steps before the numbers mean anything. shell-agents spec 5.

The doctor (`services/doctor.py`) answers "is this install operable?" for an operator
at a terminal. This answers the same question for the web, as five ordered steps with
a done state and a place to go, and hands the rest of the doctor's findings over as
`warnings`.

It deliberately does NOT run `_check_batch_coverage`, which reads every ingredient's
on-hand: the shell asks for `open_steps` on every page load, and a checklist that
takes seconds is a checklist nobody opens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    PriceSource,
    StockCount,
    Supplier,
    Tier,
)
from cafeops.services import doctor
from cafeops.services.auth import credential_source
from cafeops.services.sync_runs import LIGHTSPEED_ENV_VARS, latest_ok, lightspeed_ready

log = logging.getLogger(__name__)

__all__ = ["SetupStatus", "SetupStep", "SetupWarning", "setup_status"]

StepKey = Literal["import", "shelf_life", "supplier_terms", "lightspeed", "first_count"]


@dataclass(frozen=True, slots=True)
class SetupStep:
    n: int
    key: StepKey
    done: bool
    #: None means "not countable", which is not the same as 0.
    remaining: int | None
    title: str
    body: str
    cta_label: str | None
    cta_route: str | None
    cli_fix: str | None


@dataclass(frozen=True, slots=True)
class SetupWarning:
    severity: str
    name: str
    detail: str
    fix: str


@dataclass(frozen=True, slots=True)
class SetupStatus:
    empty_install: bool
    steps: tuple[SetupStep, ...]
    warnings: tuple[SetupWarning, ...] = field(default_factory=tuple)

    @property
    def open_steps(self) -> int:
        return sum(1 for s in self.steps if not s.done)


def _count(session: Session, stmt: Select[Any]) -> int:
    return int(session.scalar(stmt) or 0)


def _import_step(session: Session, *, with_counts: bool) -> SetupStep:
    ingredients = _count(session, select(func.count(Ingredient.id)))
    done = ingredients > 0
    body = (
        "Ingredients, prices, recipes and menu from the finance workbook. It proposes "
        "recipes; you confirm them."
    )
    remaining: int | None = None
    if done and with_counts:
        try:
            from cafeops.services.materialise_template import list_proposals

            existing = set(session.scalars(select(DrinkTemplate.name)))
            waiting = [
                p
                for p in list_proposals(session)
                if not p.is_singleton and not p.is_hollow and p.name not in existing
            ]
            remaining = len(waiting)
            body += f" {remaining} recipe proposal{'s' if remaining != 1 else ''} waiting."
        except Exception:  # the count is a nicety; the step's state does not depend on it
            log.debug("setup step 1: could not count recipe proposals", exc_info=True)
            remaining = None
    return SetupStep(
        n=1,
        key="import",
        done=done,
        remaining=remaining,
        title="Bring in the spreadsheet.",
        body=body,
        cta_label="Review recipes" if done else None,
        cta_route="#/recipes" if done else None,
        cli_fix=None if done else "uv run cafeops import-legacy --commit",
    )


def _shelf_life_step(session: Session) -> SetupStep:
    guesses = _count(
        session,
        select(func.count(Ingredient.id)).where(
            Ingredient.shelf_life_days.isnot(None),
            Ingredient.shelf_life_source == PriceSource.ESTIMATE,
            Ingredient.retired_at.is_(None),
        ),
    )
    body = "Not in the workbook. Without them, orders can't be capped to what will keep."
    if guesses:
        body += f" {guesses} {'is' if guesses == 1 else 'are'} still guesses."
    return SetupStep(
        n=2,
        key="shelf_life",
        done=guesses == 0,
        remaining=guesses,
        title="Add shelf lives for fresh stock.",
        body=body,
        cta_label="Open Stock",
        cta_route="#/stock?filter=shelf_life",
        cli_fix=None if guesses == 0 else "uv run cafeops shelf-life list",
    )


def _supplier_step(session: Session) -> SetupStep:
    placeholders = _count(
        session,
        select(func.count(Supplier.id)).where(Supplier.terms_are_placeholders.is_(True)),
    )
    body = "Delivery days, cut-offs, minimums and fees."
    if placeholders:
        body += f" {placeholders} {'is' if placeholders == 1 else 'are'} still guesses."
    return SetupStep(
        n=3,
        key="supplier_terms",
        done=placeholders == 0,
        remaining=placeholders,
        title="Confirm supplier terms.",
        body=body,
        cta_label="Open Suppliers",
        cta_route="#/suppliers",
        cli_fix=None if placeholders == 0 else "uv run cafeops supplier list",
    )


def _lightspeed_step(session: Session) -> SetupStep:
    configured = lightspeed_ready()
    synced = configured and latest_ok(session) is not None
    if not configured:
        body = (
            "So sales come in by themselves and stock is worked out from them. "
            "Connecting is done on the server, not here."
        )
    elif not synced:
        body = "Connected, but no sync has brought sales in yet. Sync now from Settings."
    else:
        body = "So sales come in by themselves and stock is worked out from them."
    return SetupStep(
        n=4,
        key="lightspeed",
        done=synced,
        remaining=None,
        title="Connect Lightspeed.",
        body=body,
        cta_label="Settings",
        cta_route="#/settings",
        cli_fix=None if configured else "Set " + ", ".join(LIGHTSPEED_ENV_VARS) + " in .env",
    )


def _first_count_step(session: Session) -> SetupStep:
    tier_a = _count(
        session,
        select(func.count(Ingredient.id)).where(
            Ingredient.tier == Tier.A, Ingredient.retired_at.is_(None)
        ),
    )
    counted = _count(
        session,
        select(func.count(func.distinct(StockCount.ingredient_id)))
        .join(Ingredient, Ingredient.id == StockCount.ingredient_id)
        .where(Ingredient.tier == Tier.A, Ingredient.retired_at.is_(None)),
    )
    remaining = max(tier_a - counted, 0)
    body = "Tier A first. Every estimate starts from a real count."
    if tier_a:
        body += f" {counted} of {tier_a} tier A counted."
    return SetupStep(
        n=5,
        key="first_count",
        done=tier_a > 0 and remaining == 0,
        remaining=remaining if tier_a else None,
        title="Do a first count.",
        body=body,
        cta_label="Start counting",
        cta_route="#/stock?mode=count&tier=A",
        cli_fix=None,
    )


#: Doctor findings the steps already cover, or that this module states better.
_COVERED = frozenset(
    {"legacy import", "supplier terms", "shelf life", "lightspeed", "api password", "schema"}
)


def _warnings(session: Session) -> tuple[SetupWarning, ...]:
    report = doctor.DoctorReport()
    doctor._check_schema(session, report)
    if report.failures:
        return tuple(
            SetupWarning(severity=c.severity.value, name=c.name, detail=c.detail, fix=c.fix)
            for c in report.failures
        )
    checks = (
        ("legacy import", doctor._check_seed, (session, report)),
        ("drift history", doctor._check_drift_backfill, (session, report)),
        ("invariant 1", doctor._check_invariant_1, (session, report)),
        ("invariant 8", doctor._check_invariant_8, (session, report)),
        ("invariant 2", doctor._check_auto_order_evidence, (session, report)),
        ("invariant 12", doctor._check_invariant_12, (session, report)),
        ("seasons", doctor._check_seasons, (session, report)),
        ("takings", doctor._check_takings, (session, report)),
        ("input trust", doctor._check_trust_of_inputs, (session, report)),
        ("credentials", doctor._check_credentials, (report,)),
    )
    for name, fn, args in checks:
        doctor._guarded(report, name, fn, *args)

    out = [
        SetupWarning(severity=c.severity.value, name=c.name, detail=c.detail, fix=c.fix)
        for c in report.checks
        if c.severity is not doctor.Severity.OK and c.name not in _COVERED
    ]
    source = credential_source(session)
    if source is None:
        out.insert(
            0,
            SetupWarning(
                severity="FAIL",
                name="password",
                detail="no back-office password is configured, so the API refuses to serve",
                fix="set CAFEOPS_API_PASSWORD in .env and restart the api service",
            ),
        )
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    out.sort(key=lambda w: order.get(w.severity, 3))
    return tuple(out)


def setup_status(
    session: Session, *, with_counts: bool = True, with_warnings: bool = True
) -> SetupStatus:
    """The five steps. `with_counts=False` skips the recipe-proposal count (pattern
    detection) and `with_warnings=False` skips the doctor, for the shell's cheap call."""
    steps = (
        _import_step(session, with_counts=with_counts),
        _shelf_life_step(session),
        _supplier_step(session),
        _lightspeed_step(session),
        _first_count_step(session),
    )
    return SetupStatus(
        empty_install=not steps[0].done,
        steps=steps,
        warnings=_warnings(session) if with_warnings else (),
    )
