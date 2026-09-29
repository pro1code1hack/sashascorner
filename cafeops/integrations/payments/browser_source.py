"""`BrowserAgentPaymentSource` -- the same takings, pulled by driving a browser.

Spec 4.6 established the shape for a gated portal: a CSV implementation first and a
browser-agent one second. Channels has both; payments had only the first, which left
it depending on somebody remembering to export a file every week.

The design is the same as `channels/browser_source.py`, and for the same reasons:

* the agent's only job is to **download the export the back office already offers**.
  It does not read figures off the screen. A screen-scraped total cannot be
  re-checked against anything, whereas a downloaded CSV goes through the *same*
  reader, the same column mapping and the same refusals as a hand export -- it
  differs only in its `source`, so a wrong figure is still traceable to a file.
* when it cannot run -- no driver wired, no credentials, the plan failed -- it
  raises `PaymentSourceUnavailable` so the caller falls back to the CSV directory
  rather than recording a half-scraped day.
* it **never** logs in on its own behalf, submits, pays or changes anything. The
  plan is navigate-and-download only, and `TakingsPlan` is inert: describing is not
  doing (invariant 10 -- the agent emits a proposal, it does not act).

Nothing here contacts Lightspeed. `BrowserDriver` is the seam a real driver plugs
into; the one shipped today reads a local fixture so the path is demonstrable with
no network and no credentials at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from cafeops.domain.enums import PaymentSourceKind
from cafeops.integrations.payments.base import (
    PaymentReport,
    PaymentSourceUnavailable,
)
from cafeops.integrations.payments.csv_source import CsvPaymentSource

#: What the agent must do to get the export. Data, not code: a moved button is a
#: string change, which is the only kind of change a portal redesign should need.
BACK_OFFICE_PLAN: tuple[str, tuple[str, ...]] = (
    "https://backoffice.lightspeedapp.com/reports/payments",
    (
        "Open the payments report for the requested date range.",
        "Set the grouping to 'by day' and 'by payment type'.",
        "Use the report's own Export / Download CSV control.",
        "Download the file. Do not transcribe any figure from the screen.",
        "Stop. Do not open any other report, and change nothing.",
    ),
)


@dataclass(frozen=True, slots=True)
class TakingsPlan:
    """What the agent is being asked to do. Inert -- describing is not doing."""

    since: date
    until: date
    target_url: str
    steps: tuple[str, ...]
    #: Stated on the plan so a reviewer can see the boundary, and enforced in code
    #: by `BrowserDriver` only ever being asked for downloaded files.
    read_only: bool = True

    def script(self) -> str:
        head = f"Read-only report download: takings {self.since}..{self.until}"
        body = "\n".join(f"{n}. {s}" for n, s in enumerate(self.steps, 1))
        return f"{head}\n{self.target_url}\n{body}"


class BrowserDriver(Protocol):
    """Whatever actually drives a browser. A real one plugs in here."""

    name: str

    def download_reports(self, plan: TakingsPlan) -> Sequence[Path]:
        """Follow the plan and return the CSV files it downloaded.

        Must raise `PaymentSourceUnavailable` on any failure -- a moved button, a
        login wall, an MFA prompt -- so the caller falls back to the CSV directory
        instead of recording a half-scraped day.
        """
        ...


@dataclass(slots=True)
class FixtureBrowserDriver:
    """A driver that downloads nothing and returns local fixture files.

    The browser path is demonstrable without touching Lightspeed. It is honest
    about itself: rows still land stamped `CSV_UPLOAD` because that is what the
    file is -- claiming an API origin for a fixture would be the silent mix
    spec 4.6 forbids.
    """

    fixtures: tuple[Path, ...] = ()
    name: str = "fixture (no network)"

    def download_reports(self, plan: TakingsPlan) -> Sequence[Path]:
        if not self.fixtures:
            raise PaymentSourceUnavailable(
                "the fixture driver has no files wired, so there is nothing to return"
            )
        return self.fixtures


@dataclass(slots=True)
class BrowserAgentPaymentSource:
    """Fetch takings by having an agent download the back office's own export."""

    driver: BrowserDriver | None = None
    kind: PaymentSourceKind = PaymentSourceKind.CSV_UPLOAD

    def plan(self, *, since: date, until: date) -> TakingsPlan:
        """What would be done. Contacts nothing; safe to print and review."""
        url, steps = BACK_OFFICE_PLAN
        return TakingsPlan(since=since, until=until, target_url=url, steps=steps)

    def fetch(self, *, since: date, until: date) -> PaymentReport:
        if self.driver is None:
            raise PaymentSourceUnavailable(
                "no browser driver is wired, so the agent cannot fetch anything. "
                "Export the payment report by hand and use the CSV source, or set "
                "CAFEOPS_PAYMENTS_CSV_DIR."
            )
        plan = self.plan(since=since, until=until)
        paths = self.driver.download_reports(plan)
        if not paths:
            raise PaymentSourceUnavailable(
                f"{self.driver.name} returned no files for {since}..{until}"
            )
        # The downloaded files go through the SAME reader as a hand export: same
        # column mapping, same refusals, same blank-vs-zero rule. That is the whole
        # point of downloading rather than scraping.
        directory = paths[0].parent
        report = CsvPaymentSource(directory).fetch(since=since, until=until)
        return PaymentReport(
            kind=report.kind,
            since=report.since,
            until=report.until,
            rows=report.rows,
            rejected=report.rejected,
            unmapped=report.unmapped,
            notes=(
                *report.notes,
                f"downloaded by {self.driver.name}; parsed by the same reader as a "
                "hand export, so the same columns were required and the same rows refused",
            ),
        )


__all__ = [
    "BACK_OFFICE_PLAN",
    "BrowserAgentPaymentSource",
    "BrowserDriver",
    "FixtureBrowserDriver",
    "TakingsPlan",
]
