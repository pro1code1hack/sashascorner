"""Turn stored sales into stock movements. The idempotency key is on the row.

`sale.expanded_at` is set in the same transaction as the movements
(`ARCHITECTURE.md` 2.4), so `pending_expansion` cannot return a sale twice and a late run
covering three days writes each sale's depletion exactly once. This job therefore needs no
"last run" bookkeeping of its own -- which is the right shape, because a job that remembers
when it last ran is a job that double-writes the first time that memory is lost.

Two things happen alongside, both deliberately inside expansion rather than after it:

* **Expiry is swept before stock is allocated.** `expand_recipes._BatchAllocator` does
  this per ingredient. Allocating first would quietly sell expired stock and the loss
  would never appear in the ledger (`ARCHITECTURE.md` 8F.2).
* **Voided receipts are reversed.** A receipt voided after expansion has to give its
  movements back, and `reverse_voided_expansions` is idempotent on its own marker.

A sale that cannot be expanded -- a `SUBSTITUTE` into a non-substitutable slot -- is left
pending on purpose. It is a data error a person has to fix, and expanding it "somehow"
would put the wrong ingredient in an append-only ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from cafeops.services.expand_recipes import (
    ExpansionReport,
    ReversalReport,
    expand_pending,
    reverse_voided_expansions,
)

__all__ = ["NightlyExpandReport", "run_nightly_expand"]


@dataclass
class NightlyExpandReport:
    expansion: ExpansionReport | None = None
    reversal: ReversalReport | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts: list[str] = []
        if self.reversal is not None and self.reversal.sales_reversed:
            parts.append(f"reversed {self.reversal.sales_reversed} voided sale(s)")
        if self.expansion is not None:
            parts.append(self.expansion.summary())
        return "; ".join(parts) or "nothing to expand"

    @property
    def blocked(self) -> list[int]:
        return [] if self.expansion is None else list(self.expansion.blocked_sale_ids)


def run_nightly_expand(
    session: Session, *, limit: int | None = None, reverse_voided: bool = True
) -> NightlyExpandReport:
    """Reverse voided receipts, then expand what is pending.

    Reversal first: a receipt voided after it was expanded still holds movements, and
    expanding new sales before giving those back would leave the ledger briefly claiming
    stock that two contradictory rows both account for.
    """
    report = NightlyExpandReport()
    if reverse_voided:
        report.reversal = reverse_voided_expansions(session)
    report.expansion = expand_pending(session, limit=limit)
    if report.expansion.blocked_sale_ids:
        report.warnings.append(
            f"{len(report.expansion.blocked_sale_ids)} sale line(s) could not be expanded "
            "and were LEFT PENDING for a human. They are a composition error, not a "
            "transient failure, so retrying will not clear them."
        )
    if report.expansion.shortfall_movements:
        report.warnings.append(
            f"{report.expansion.shortfall_movements} movement(s) consumed more than any "
            "batch could account for. Recorded, not refused: the stock left the building. "
            "It means a count or a delivery entry is wrong."
        )
    return report
