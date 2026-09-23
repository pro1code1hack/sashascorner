"""The weekly drift report: how far the ledger is from the shelf, and WHICH problem it is.

Spec 5.2's second half is the part that earns this job. A drift percentage on its own is
not actionable, because the two things that cause it have **opposite fixes**: stock that
expired means over-ordering (order less, more often), and everything else means the recipe
or the waste factor is wrong (change the recipe -- cutting the order here causes a stockout
with the gap still there). `explain_drift_history` splits them, and this report carries the
split rather than the single number.

## Idempotency

Two halves, both safe to run late or twice.

* **The backfill writes, and its key is the absence of a row.**
  `counts_missing_observations` returns only `stock_count` rows with no
  `drift_observation`, so a second run finds nothing. It is included because a count taken
  by the bot always gets its observation immediately (`record_count` does all four steps in
  one transaction); the backfill exists for counts that predate drift -- the demo seed's 72
  -- and for a count written by a path that crashed between steps.
* **The report itself writes nothing.** Every figure is recomputed from the ledger, which
  is append-only, so the same window always produces the same answer.

`gate_status` is read-only by construction: it evaluates the gate and returns the verdict
without applying it. Applying a gate decision belongs to `record_count`, at the moment
there is new evidence -- a weekly report that silently revoked auto-ordering would change
system behaviour from a document nobody had read yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from cafeops.bot.viewmodels import DriftAlertView
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.par import SqlParLevelRepository
from cafeops.domain.drift import DriftCause
from cafeops.domain.tiers import GateAction
from cafeops.domain.types import GateAlertLevel, Tier
from cafeops.services.record_count import (
    backfill_drift_observations,
    explain_drift_history,
    gate_status,
)

__all__ = ["REVOCATION_WINDOW_DAYS", "DriftReport", "run_drift_report"]

#: How far back this report looks for revocations. A week, because the report is weekly:
#: every revocation should appear in exactly one of them.
REVOCATION_WINDOW_DAYS = 7


@dataclass
class DriftReport:
    generated_at: datetime
    rows: list[DriftAlertView] = field(default_factory=list)
    observations_backfilled: int = 0
    counts_seen: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def alerts(self) -> list[DriftAlertView]:
        """Rows where the FIGURES cannot be trusted (>15%). Spec 5.2's alarm.

        Deliberately `ALARM` rather than `row.alert`: a revoke in the 10-15% band now also
        has to be heard, but it is a statement about BEHAVIOUR, not about the numbers.
        Folding it in here would put "your stock figures are untrustworthy" next to an
        ingredient whose gap is still inside the working band.
        """
        return [row for row in self.rows if row.alert_level is GateAlertLevel.ALARM]

    @property
    def notices(self) -> list[DriftAlertView]:
        """Rows that lost auto-ordering without the figures being in doubt."""
        return [row for row in self.rows if row.alert_level is GateAlertLevel.NOTICE]

    @property
    def revoked(self) -> list[DriftAlertView]:
        return [row for row in self.rows if row.gate_action is GateAction.REVOKE]

    @property
    def over_ordering(self) -> list[DriftAlertView]:
        """Gaps expiry explains. The fix is a smaller, more frequent order."""
        return [row for row in self.rows if row.cause is DriftCause.EXPIRY]

    @property
    def recipe_problems(self) -> list[DriftAlertView]:
        """Gaps expiry does NOT explain. Cutting the order here causes a stockout."""
        return [row for row in self.rows if row.cause is DriftCause.MEASUREMENT]

    def summary(self) -> str:
        parts = [f"drift_report {self.generated_at:%Y-%m-%d}", f"{len(self.rows)} ingredient(s)"]
        if self.observations_backfilled:
            parts.append(
                f"backfilled {self.observations_backfilled} of {self.counts_seen} count(s) "
                "that had no drift observation"
            )
        if self.alerts:
            parts.append(f"{len(self.alerts)} ALERT(s) over 15%")
        if self.over_ordering:
            parts.append(f"{len(self.over_ordering)} explained by expiry (over-ordering)")
        if self.recipe_problems:
            parts.append(f"{len(self.recipe_problems)} not explained by expiry (recipe)")
        return "; ".join(parts)


def run_drift_report(
    session: Session,
    *,
    at: datetime | None = None,
    tiers: tuple[Tier, ...] = (Tier.A, Tier.B),
    backfill: bool = True,
    only_interesting: bool = True,
    revocation_window_days: int = REVOCATION_WINDOW_DAYS,
) -> DriftReport:
    """Attribute every tracked ingredient's latest drift, and say which fix applies.

    `only_interesting=True` keeps the report to what somebody would act on: an alert, a
    revocation, a tuning-band verdict, or a gap expiry explains. Forty rows saying
    "in order" is how a weekly report stops being read.
    """
    at = at or datetime.now(UTC)
    revoked_window = timedelta(days=revocation_window_days)
    report = DriftReport(generated_at=at)

    if backfill:
        filled = backfill_drift_observations(session, decided_at=at)
        report.counts_seen = filled.counts_seen
        report.observations_backfilled = filled.observations_written
        report.warnings.extend(filled.warnings)

    ingredients = SqlIngredientRepository(session)
    par_repo = SqlParLevelRepository(session)

    for snapshot in ingredients.list_tracked(tiers=tiers):
        decision = gate_status(session, ingredient=snapshot)
        history = explain_drift_history(session, ingredient_id=snapshot.id, limit=1)
        latest = history[0] if history else None
        par = par_repo.get(snapshot.id)
        row = DriftAlertView(
            ingredient_id=snapshot.id,
            name=snapshot.name,
            unit=snapshot.unit,
            drift_pct=decision.considered_pcts[0] if decision.considered_pcts else None,
            verdict=decision.verdict,
            gate_action=decision.action,
            auto_order_enabled=bool(par and par.auto_order_enabled),
            alert=decision.alert,
            clean_streak=decision.clean_streak,
            required_streak=decision.required_streak,
            cause=None if latest is None else latest.cause,
            expiry_share=None if latest is None else latest.explanation.expiry_share,
            alert_level=decision.alert_level,
            revoke_cause=decision.revoke_cause,
        )
        # The gate is STATELESS: `gate_status` re-derives from today's history, so an
        # ingredient revoked on Tuesday reports HOLD by Friday and the revocation has
        # vanished from a weekly report entirely. `par_level` is the only record of the
        # event, so it is read here as well -- the live decision still wins when it is
        # itself a revoke, because that is this moment's verdict rather than a memory.
        if row.gate_action is not GateAction.REVOKE:
            row = _carry_stored_revocation(par_repo, row, since=at - revoked_window)
        if only_interesting and not _interesting(row):
            continue
        report.rows.append(row)

    stale = [row for row in report.rows if row.cause is None]
    if stale:
        report.warnings.append(
            f"{len(stale)} ingredient(s) have no drift observation at all, so there is "
            "nothing to attribute. They have never been counted twice -- which is itself "
            "the finding, because invariant 2 needs two consecutive clean counts before "
            "anything can be auto-ordered."
        )
    return report


def _carry_stored_revocation(
    par_repo: SqlParLevelRepository, row: DriftAlertView, *, since: datetime
) -> DriftAlertView:
    """Fill in a revocation the live gate can no longer see. Returns the row either way.

    Only when auto-ordering is still OFF: a grant clears `auto_order_revoke_cause`, so a
    stored cause on an enabled ingredient would be a revocation that has since been earned
    back -- announcing it would be worse than silence.
    """
    audit = par_repo.audit(row.ingredient_id)
    if (
        audit is None
        or audit.revoke_cause is None
        or audit.revoked_at is None
        or audit.auto_order_enabled
        or audit.revoked_at < since
    ):
        return row
    return replace(
        row,
        revoke_cause=audit.revoke_cause,
        # Never DOWNGRADE: a >15% gap is an alarm about the figures whatever else is true.
        alert_level=(
            row.alert_level if row.alert_level is GateAlertLevel.ALARM else GateAlertLevel.NOTICE
        ),
    )


def _interesting(row: DriftAlertView) -> bool:
    if row.alert or row.revoke_cause is not None or row.gate_action is not GateAction.HOLD:
        return True
    if row.cause in (DriftCause.EXPIRY, DriftCause.MIXED):
        return True
    return bool(row.verdict is not None and row.verdict.value != "ELIGIBLE")
