"""What the frame needs on every page: sync line, nav badges, banners. shell-agents 3.

**One cheap call.** It must never run `stock_view` or `draft_orders_view` (seconds
each). It counts rows, reads `sync_run`, and asks the finance service for its cash alert,
nothing more.

Two banners, and only two -- CLAUDE.md §10 rejects the "operations console" and the
Stock screen owns its own alerts:

* **Stale Lightspeed sync**: more than `SYNC_STALE_HOURS` since the last LIVE sync
  that brought sales in (`sync_run`, never `max(sale.sold_at)` -- see sync_runs.py),
  or "not connected" when there are no credentials.
* **Cash discrepancy**: the newest trading day whose counted cash differs from what
  the till says by more than `finance_setting.cash_tolerance_pence`, with nobody's
  explanation recorded.

The cash rule lives in one place, `services.finance.overview.finance_alerts` (the same
answer `GET /api/finance/alerts` gives); this module only turns it into a banner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    AgentProposal,
    POStatus,
    ProposalStatus,
    PurchaseOrder,
    Sale,
)
from cafeops.services.finance.overview import finance_alerts
from cafeops.services.setup_status import setup_status
from cafeops.services.sync_runs import latest_attempt, latest_ok, lightspeed_ready

__all__ = [
    "SYNC_STALE_HOURS",
    "BannerView",
    "ShellStatus",
    "SyncStatusView",
    "shell_status",
]

#: Design constant (shell-agents 3.1). Integrator: move to config as
#: `CAFEOPS_SYNC_STALE_HOURS` if it ever needs to differ per deployment.
SYNC_STALE_HOURS = 12


@dataclass(frozen=True, slots=True)
class SyncAttempt:
    status: str
    finished_at: datetime | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class SyncStatusView:
    lightspeed_configured: bool
    last_ok_finished_at: datetime | None
    last_attempt: SyncAttempt | None
    last_sale_at: datetime | None
    stale_after_hours: int
    is_stale: bool


@dataclass(frozen=True, slots=True)
class BannerView:
    id: str
    instance_key: str
    tone: str
    text: str
    action_label: str | None
    action_kind: str | None
    action_route: str | None


@dataclass(frozen=True, slots=True)
class ShellStatus:
    sync: SyncStatusView
    orders_waiting: int
    proposals_waiting: int
    banners: tuple[BannerView, ...]
    empty_install: bool
    open_steps: int


# --------------------------------------------------------------------------
# formatting (server-authored English, shell-agents 3.4)
# --------------------------------------------------------------------------


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


# --------------------------------------------------------------------------
# the frame
# --------------------------------------------------------------------------


def _sync_view(session: Session, now: datetime) -> SyncStatusView:
    configured = lightspeed_ready()
    ok = latest_ok(session)
    attempt = latest_attempt(session)
    last_sale = session.scalar(select(func.max(Sale.sold_at)))
    ok_at = ok.finished_at if ok is not None else None
    stale = configured and (ok_at is None or now - ok_at > timedelta(hours=SYNC_STALE_HOURS))
    return SyncStatusView(
        lightspeed_configured=configured,
        last_ok_finished_at=ok_at,
        last_attempt=(
            None
            if attempt is None
            else SyncAttempt(
                status=attempt.status.value, finished_at=attempt.finished_at, detail=attempt.detail
            )
        ),
        last_sale_at=last_sale,
        stale_after_hours=SYNC_STALE_HOURS,
        is_stale=stale,
    )


def _banners(session: Session, sync: SyncStatusView, now: datetime) -> list[BannerView]:
    out: list[BannerView] = []
    if not sync.lightspeed_configured:
        out.append(
            BannerView(
                id="lightspeed_not_connected",
                instance_key="lightspeed_not_connected",
                tone="stale",
                text=(
                    "Lightspeed isn't connected, so sales aren't coming in by themselves. "
                    "Stock is worked out from what was imported by hand."
                ),
                action_label="Settings",
                action_kind="navigate",
                action_route="#/settings",
            )
        )
    elif sync.is_stale:
        if sync.last_ok_finished_at is None:
            text = (
                "Lightspeed is connected but no sync has brought sales in yet. Stock "
                "estimates and takings are behind until one does."
            )
            key = "stale:never"
        else:
            text = (
                f"Lightspeed sales last came in {_ago(now - sync.last_ok_finished_at)}. "
                "Stock estimates and takings are behind until the next sync."
            )
            key = f"stale:{sync.last_ok_finished_at:%Y-%m-%dT%H:%MZ}"
        out.append(
            BannerView(
                id="stale_sync",
                instance_key=key,
                tone="stale",
                text=text,
                action_label="Sync now",
                action_kind="sync_now",
                action_route=None,
            )
        )

    cash = finance_alerts(session).cash
    if cash is not None:
        out.append(
            BannerView(
                id="cash_discrepancy",
                instance_key=f"cash:{cash.date.isoformat()}",
                tone="alert",
                text=cash.message,
                action_label="Look at it",
                action_kind="navigate",
                action_route=f"#/money/reconcile?month={cash.date:%Y-%m}&date={cash.date.isoformat()}",
            )
        )
    return out


def shell_status(session: Session, *, now: datetime | None = None) -> ShellStatus:
    now = now or datetime.now(UTC)
    sync = _sync_view(session, now)
    orders_waiting = int(
        session.scalar(
            select(func.count(PurchaseOrder.id)).where(
                PurchaseOrder.status == POStatus.PENDING_CONFIRM
            )
        )
        or 0
    )
    proposals_waiting = int(
        session.scalar(
            select(func.count(AgentProposal.id)).where(
                AgentProposal.status == ProposalStatus.WAITING
            )
        )
        or 0
    )
    setup = setup_status(session, with_counts=False, with_warnings=False)
    return ShellStatus(
        sync=sync,
        orders_waiting=orders_waiting,
        proposals_waiting=proposals_waiting,
        banners=tuple(_banners(session, sync, now)),
        empty_install=setup.empty_install,
        open_steps=setup.open_steps,
    )
