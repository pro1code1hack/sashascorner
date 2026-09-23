"""Is this install actually operable? Spec's runbook question, as a command.

`docs/OPERATIONS.md` asks "what do I check when a screen is blank". This answers it
without a human remembering the list.

Two things it is deliberately NOT:

- **Not a test suite.** No test suite exists here (ARCHITECTURE 1) and this is not a
  substitute for one. It inspects the live database an operator actually has, which is
  the opposite of what a test does.
- **Not a linter for data quality.** It reports the handful of conditions that make the
  product give wrong answers or refuse to start, and stays quiet about everything else.
  A check that fires constantly teaches people to ignore the output.

Severity is the whole design. FAIL means the system is producing wrong numbers or will
not run. WARN means a number is resting on a guess the owner should replace. INFO is
context. Only FAIL sets the exit code, so this is safe to run from cron.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    DriftObservation,
    Ingredient,
    IngredientPrice,
    MenuItem,
    MenuItemCost,
    ParLevel,
    POStatus,
    PriceSource,
    PurchaseOrder,
    Sale,
    Supplier,
)
from cafeops.services.read_stock import read_on_hand

__all__ = ["Check", "DoctorReport", "Severity", "run_doctor"]


class Severity(enum.StrEnum):
    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"
    OK = "OK"


@dataclass(frozen=True, slots=True)
class Check:
    severity: Severity
    name: str
    detail: str
    #: What to actually do about it. Empty for OK and INFO.
    fix: str = ""


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, severity: Severity, name: str, detail: str, fix: str = "") -> None:
        self.checks.append(Check(severity=severity, name=name, detail=detail, fix=fix))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.severity is Severity.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.severity is Severity.WARN]

    @property
    def is_operable(self) -> bool:
        return not self.failures

    def exit_code(self) -> int:
        """Non-zero only on FAIL, so cron does not page anyone over a WARN."""
        return 1 if self.failures else 0


def _guarded(r: DoctorReport, name: str, fn: object, *args: object) -> None:
    """Run one check; report a crash as a finding rather than losing the whole report.

    A doctor that dies on its third check tells the operator less than one that says
    "this check broke" and carries on to the other nine.
    """
    try:
        fn(*args)  # type: ignore[operator]
    except Exception as exc:
        r.add(
            Severity.WARN,
            name,
            f"this check could not run: {type(exc).__name__}: {exc}",
            "the finding is the crash itself -- the other checks below still apply",
        )


def run_doctor(session: Session, *, as_of: datetime | None = None) -> DoctorReport:
    at = as_of or datetime.now(UTC)
    r = DoctorReport()

    # The schema check gates everything after it. Without tables, every later check
    # raises "no such table" -- so the doctor would crash on precisely the case it most
    # needs to diagnose: a fresh install nobody has migrated yet.
    _check_schema(session, r)
    if r.failures:
        r.add(
            Severity.INFO,
            "remaining checks",
            "skipped: there is no schema to inspect yet. Migrate, then run this again",
        )
        return r

    _guarded(r, "legacy import", _check_seed, session, r)
    _guarded(r, "drift history", _check_drift_backfill, session, r)
    _guarded(r, "batch coverage", _check_batch_coverage, session, r, at)
    _guarded(r, "invariant 1", _check_invariant_1, session, r)
    _guarded(r, "invariant 8", _check_invariant_8, session, r)
    _guarded(r, "invariant 2", _check_auto_order_evidence, session, r)
    _guarded(r, "input trust", _check_trust_of_inputs, session, r)
    _guarded(r, "credentials", _check_credentials, r)
    return r


# --------------------------------------------------------------------------


def _check_schema(session: Session, r: DoctorReport) -> None:
    from sqlalchemy import text

    try:
        row = session.execute(text("SELECT version_num FROM alembic_version")).first()
    except Exception:
        r.add(
            Severity.FAIL,
            "schema",
            "no alembic_version table: this database has never been migrated",
            "uv run alembic upgrade head",
        )
        return
    if row is None:
        r.add(
            Severity.FAIL,
            "schema",
            "alembic_version is empty",
            "uv run alembic upgrade head",
        )
        return
    r.add(Severity.OK, "schema", f"migrated, at revision {row[0][:12]}")


def _check_seed(session: Session, r: DoctorReport) -> None:
    ingredients = int(session.scalar(select(func.count(Ingredient.id))) or 0)
    items = int(session.scalar(select(func.count(MenuItem.id))) or 0)
    suppliers = int(session.scalar(select(func.count(Supplier.id))) or 0)
    if ingredients == 0:
        r.add(
            Severity.FAIL,
            "legacy import",
            "no ingredients: the workbook has never been imported, so nothing can be "
            "costed, forecast or ordered",
            "uv run cafeops import-legacy --commit",
        )
        return
    r.add(
        Severity.OK,
        "legacy import",
        f"{ingredients} ingredients, {items} menu items, {suppliers} suppliers",
    )
    sales = int(session.scalar(select(func.count(Sale.id))) or 0)
    if sales == 0:
        r.add(
            Severity.WARN,
            "sales history",
            "no sales at all: every forecast will be low-confidence and no consumption "
            "has been derived",
            "uv run cafeops sync --from <date> --to <date>, or seed --demo for a trial",
        )


def _check_drift_backfill(session: Session, r: DoctorReport) -> None:
    counts_without = session.execute(
        select(func.count()).select_from(select(DriftObservation.stock_count_id).subquery())
    ).scalar()
    observations = int(session.scalar(select(func.count(DriftObservation.id))) or 0)
    if observations == 0:
        r.add(
            Severity.WARN,
            "drift history",
            "no drift observations: the auto-order gate has no evidence to act on, so "
            "nothing can earn auto-ordering",
            "uv run cafeops drift --backfill  (REQUIRED after any reseed)",
        )
        return
    r.add(Severity.OK, "drift history", f"{observations} observations recorded")
    _ = counts_without


def _check_batch_coverage(session: Session, r: DoctorReport, at: datetime) -> None:
    """The check that exists because this regressed silently once.

    Unbatched stock is invisible to FIFO and to the expiry sweep, so it can never expire
    and never be counted as waste -- it simply vanishes from the P&L.
    """
    gaps: list[tuple[str, Decimal]] = []
    for reading in read_on_hand(session, as_of=at):
        gap = reading.batch_coverage_gap
        if abs(gap) > Decimal("0.001"):
            gaps.append((reading.ingredient.name, gap))
    if not gaps:
        r.add(Severity.OK, "batch coverage", "every tracked ingredient reconciles")
        return
    worst = sorted(gaps, key=lambda g: abs(g[1]), reverse=True)[:4]
    shown = ", ".join(f"{n} {g:+}" for n, g in worst)
    r.add(
        Severity.FAIL,
        "batch coverage",
        f"{len(gaps)} ingredient(s) hold stock no batch accounts for ({shown}). That "
        "quantity can never expire and never be counted as waste",
        "uv run cafeops rebuild-batches, or receive the missing delivery",
    )


def _check_invariant_1(session: Session, r: DoctorReport) -> None:
    bad = int(
        session.scalar(
            select(func.count(PurchaseOrder.id)).where(
                PurchaseOrder.status.in_([POStatus.CONFIRMED, POStatus.SENT, POStatus.RECEIVED]),
                PurchaseOrder.confirmed_by.is_(None),
            )
        )
        or 0
    )
    if bad:
        r.add(
            Severity.FAIL,
            "invariant 1",
            f"{bad} order(s) advanced past DRAFT with no named human. A CHECK constraint "
            "should make this impossible, so the schema has been bypassed",
            "investigate before trusting any order; do not clear it by hand",
        )
        return
    drafts = int(
        session.scalar(
            select(func.count(PurchaseOrder.id)).where(PurchaseOrder.status == POStatus.DRAFT)
        )
        or 0
    )
    r.add(
        Severity.OK,
        "invariant 1",
        f"no order confirmed without a human ({drafts} awaiting confirmation)",
    )


def _check_invariant_8(session: Session, r: DoctorReport) -> None:
    zeroed = int(
        session.scalar(
            select(func.count(MenuItemCost.id)).where(
                MenuItemCost.cost_pence == 0, MenuItemCost.has_missing_cost.is_(True)
            )
        )
        or 0
    )
    if zeroed:
        r.add(
            Severity.FAIL,
            "invariant 8",
            f"{zeroed} item(s) record a missing cost as 0 rather than NULL, so they are "
            "flattering every margin and COGS figure they appear in",
            "uv run cafeops cost-rollup",
        )
        return
    estimated = int(
        session.scalar(
            select(func.count(MenuItemCost.id)).where(
                MenuItemCost.cost_source == PriceSource.ESTIMATE
            )
        )
        or 0
    )
    total = int(session.scalar(select(func.count(MenuItemCost.id))) or 0)
    r.add(
        Severity.OK,
        "invariant 8",
        f"no missing cost recorded as zero ({estimated} of {total} costed items rest on "
        "an ESTIMATE price)",
    )


def _check_auto_order_evidence(session: Session, r: DoctorReport) -> None:
    enabled = list(session.scalars(select(ParLevel).where(ParLevel.auto_order_enabled.is_(True))))
    unevidenced = [p for p in enabled if p.auto_order_granted_at is None]
    if unevidenced:
        r.add(
            Severity.FAIL,
            "invariant 2",
            f"{len(unevidenced)} ingredient(s) have auto-ordering on with no record of "
            "when or why it was granted, so it was set by hand rather than earned",
            "revoke it and let the drift gate grant it: uv run cafeops drift",
        )
        return
    r.add(
        Severity.OK,
        "invariant 2",
        f"{len(enabled)} ingredient(s) have earned auto-ordering, all with an audit trail",
    )


def _check_trust_of_inputs(session: Session, r: DoctorReport) -> None:
    """The two guesses that quietly decide real quantities."""
    placeholders = list(
        session.scalars(select(Supplier).where(Supplier.terms_are_placeholders.is_(True)))
    )
    if placeholders:
        names = ", ".join(s.name for s in placeholders)
        r.add(
            Severity.WARN,
            "supplier terms",
            f"{len(placeholders)} supplier(s) have INVENTED lead times, delivery days, "
            f"cutoffs and minimums: {names}. Every quantity on their orders rests on "
            "those guesses",
            "confirm with each supplier, then edit cafeops/seed/suppliers.py",
        )
    estimated_life = int(
        session.scalar(
            select(func.count(Ingredient.id)).where(
                Ingredient.shelf_life_days.isnot(None),
                Ingredient.shelf_life_source == PriceSource.ESTIMATE,
            )
        )
        or 0
    )
    if estimated_life:
        r.add(
            Severity.WARN,
            "shelf life",
            f"{estimated_life} perishable(s) have an ESTIMATE shelf life, not a measured "
            "one. Shelf life CAPS order size, so a wrong one either wastes stock or "
            "causes a stockout",
            "confirm the perishables that actually move, starting with the milks",
        )
    estimated_price = int(
        session.scalar(
            select(func.count(IngredientPrice.id)).where(
                IngredientPrice.source == PriceSource.ESTIMATE,
                IngredientPrice.effective_to.is_(None),
            )
        )
        or 0
    )
    if estimated_price:
        r.add(
            Severity.INFO,
            "prices",
            f"{estimated_price} current price(s) are ESTIMATE. They stay flagged through "
            "every rollup and are excluded from margin aggregates, so nothing is being "
            "presented as more certain than it is",
        )


def _check_credentials(r: DoctorReport) -> None:
    if not settings.api_password:
        r.add(
            Severity.WARN,
            "api password",
            "CAFEOPS_API_PASSWORD is unset, so the API refuses to serve anything but "
            "/api/health. This is the usual reason a dashboard screen is blank",
            "set it in .env and restart the api service",
        )
    else:
        r.add(Severity.OK, "api password", "set, so the API will serve")

    if not settings.lightspeed_configured:
        r.add(
            Severity.INFO,
            "lightspeed",
            "no credentials: running on fixtures. Sales are whatever was seeded or "
            "imported by hand",
        )
    if not settings.telegram_bot_token:
        r.add(
            Severity.INFO,
            "telegram",
            "no bot token: the bot cannot start, and nothing has ever been sent. "
            "`cafeops bot-preview` renders every flow locally",
        )
