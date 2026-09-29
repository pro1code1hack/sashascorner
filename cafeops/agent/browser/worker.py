"""The queue consumer: `cafeops browser-worker`.

docs/agents/BROWSER-ORDERING.md §4 step 2 and §8. The only process that opens a
browser. It polls `browser_job`, claims the oldest QUEUED row with a conditional
UPDATE (so two workers cannot take the same job), runs it through `job.py`, and
marks it finished. SQLite has one writer, so every transaction here is one short
statement: claim, heartbeat, finish. The long part -- the browser -- happens between
them with no transaction open.

A heartbeat thread touches `heartbeat_at` every few seconds while a job runs, on its
own session, so a model call or a slow page does not make a live worker look dead.
On start (and every poll) RUNNING jobs whose heartbeat is older than
`settings.browser_heartbeat_stale_seconds` are FAILED as orphans of a dead worker.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.db.models import BrowserJob, BrowserJobKind, BrowserJobStatus

log = logging.getLogger("cafeops.browser.worker")

HEARTBEAT_EVERY_SECONDS = 10


def _now() -> datetime:
    return datetime.now(UTC)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# ==========================================================================
# The three short transactions
# ==========================================================================


def fail_stale_jobs(factory: sessionmaker[Session]) -> int:
    """RUNNING jobs nobody has touched for too long are a dead worker's. Fail them."""
    cutoff = _now() - timedelta(seconds=settings.browser_heartbeat_stale_seconds)
    with session_scope(factory) as session:
        stale = list(
            session.scalars(
                select(BrowserJob).where(
                    BrowserJob.status == BrowserJobStatus.RUNNING,
                    BrowserJob.heartbeat_at.is_not(None),
                    BrowserJob.heartbeat_at < cutoff,
                )
            )
        )
        for job in stale:
            job.status = BrowserJobStatus.FAILED
            job.error = "worker died (stale heartbeat)"
            job.finished_at = _now()
            log.warning("job %s failed: stale heartbeat (worker %s)", job.id, job.worker_id)
        return len(stale)


def claim_next(factory: sessionmaker[Session], *, worker_id: str) -> int | None:
    """Claim the oldest QUEUED job atomically; None when the queue is empty."""
    with session_scope(factory) as session:
        job_id = session.scalar(
            select(BrowserJob.id)
            .where(BrowserJob.status == BrowserJobStatus.QUEUED)
            .order_by(BrowserJob.created_at, BrowserJob.id)
            .limit(1)
        )
        if job_id is None:
            return None
        now = _now()
        result = session.execute(
            update(BrowserJob)
            .where(BrowserJob.id == job_id, BrowserJob.status == BrowserJobStatus.QUEUED)
            .values(
                status=BrowserJobStatus.RUNNING,
                started_at=now,
                heartbeat_at=now,
                worker_id=worker_id[:80],
            )
        )
        if result.rowcount != 1:  # somebody else took it between the two statements
            return None
        return int(job_id)


def _mark_failed(factory: sessionmaker[Session], job_id: int, error: str) -> None:
    with session_scope(factory) as session:
        job = session.get(BrowserJob, job_id)
        if job is None or job.status is not BrowserJobStatus.RUNNING:
            return
        job.status = BrowserJobStatus.FAILED
        job.error = error[:2000]
        job.finished_at = _now()


class _Heartbeat:
    """Touches `heartbeat_at` on its own session until stopped."""

    def __init__(self, factory: sessionmaker[Session], job_id: int) -> None:
        self.factory = factory
        self.job_id = job_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=HEARTBEAT_EVERY_SECONDS + 1)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_EVERY_SECONDS):
            try:
                with session_scope(self.factory) as session:
                    session.execute(
                        update(BrowserJob)
                        .where(
                            BrowserJob.id == self.job_id,
                            BrowserJob.status == BrowserJobStatus.RUNNING,
                        )
                        .values(heartbeat_at=_now())
                    )
            except Exception:  # a missed beat is not fatal; the next one may land
                log.warning("job %s: heartbeat failed", self.job_id, exc_info=True)


# ==========================================================================
# Running one job
# ==========================================================================


def run_job(factory: sessionmaker[Session], job_id: int) -> BrowserJobStatus:
    """Dispatch a claimed job by kind. Any escaped exception makes it FAILED."""
    from cafeops.agent.browser import job as job_module

    try:
        with _Heartbeat(factory, job_id), session_scope(factory) as session:
            job = session.get(BrowserJob, job_id)
            if job is None:
                raise LookupError(f"job {job_id} vanished after being claimed")
            log.info(
                "job %s: %s for supplier %s (%s)",
                job.id,
                job.kind.value,
                job.supplier_id,
                job.requested_by,
            )
            if job.kind is BrowserJobKind.STAGE_BASKET:
                job_module.run_stage_basket(session, job)
            elif job.kind is BrowserJobKind.CHECK_SESSION:
                job_module.run_check_session(session, job)
            else:  # pragma: no cover - the enum has two members
                raise ValueError(f"unknown job kind {job.kind!r}")
            status = job.status
    except Exception:
        last = traceback.format_exc().strip().splitlines()[-1]
        log.exception("job %s failed", job_id)
        _mark_failed(factory, job_id, last)
        return BrowserJobStatus.FAILED
    log.info("job %s finished: %s", job_id, status.value)
    return status


# ==========================================================================
# The loop
# ==========================================================================


def run_worker(
    *,
    factory: sessionmaker[Session] | None = None,
    once: bool = False,
    worker_id: str | None = None,
    poll_seconds: int | None = None,
) -> int:
    """Consume the queue. `once=True` runs at most one job and returns how many ran."""
    factory = factory or SessionFactory
    worker_id = worker_id or default_worker_id()
    poll = poll_seconds if poll_seconds is not None else settings.browser_worker_poll_seconds
    if not settings.browser_worker_enabled:
        log.warning(
            "CAFEOPS_BROWSER_WORKER_ENABLED is false: nothing can be queued, so this worker "
            "will only fail stale jobs and drain what is already queued"
        )
    log.info("browser worker %s started (poll %ss)", worker_id, poll)
    ran = 0
    try:
        while True:
            fail_stale_jobs(factory)
            job_id = claim_next(factory, worker_id=worker_id)
            if job_id is None:
                if once:
                    return ran
                time.sleep(max(1, poll))
                continue
            run_job(factory, job_id)
            ran += 1
            if once:
                return ran
    except KeyboardInterrupt:
        log.info("browser worker %s stopping after %d job(s)", worker_id, ran)
        return ran


__all__ = [
    "claim_next",
    "default_worker_id",
    "fail_stale_jobs",
    "run_job",
    "run_worker",
]
