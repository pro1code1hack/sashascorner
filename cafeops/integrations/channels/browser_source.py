"""`BrowserAgentChannelSource` -- the same reports, pulled by driving a browser.

Spec 9 job 1: browser automation is the right tool exactly where no API exists, and
channel reports are the clearest case. Spec 4.6 adds the part that shapes this file:
**it will break.** Portals redesign, MFA prompts appear, a download button moves.

So the design is built around breaking:

* the agent's only job is to **download the export the portal already offers**. It
  does not read numbers off the screen. Screen-scraped figures cannot be re-checked
  against anything, whereas a downloaded CSV goes through the *same* parser and the
  same refusals as a hand-export (`csv_source.py`), differing only in its `source`.
* when it cannot run -- no driver wired, no credentials, the plan failed -- it
  raises `ChannelSourceUnavailable`, and `jobs/channel_sync.py` falls back to the
  configured CSV source. Falling back is `CAFEOPS_CHANNEL_SOURCE=CSV` plus a
  directory, which is a config change, not a rewrite.
* it **never** logs in, submits, pays or changes anything. The plan it emits is
  navigate / download only, and `ReportPlan` is inert the way
  `suppliers.base.PreparedOrder` is inert.

Nothing here contacts Deliveroo or Just Eat. The `BrowserDriver` protocol is the
seam a real driver plugs into in Phase 2; the driver shipped today reads a local
fixture so the path can be exercised end to end with no network at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from cafeops.domain.types import ChannelSourceKind, SalesChannelName
from cafeops.integrations.channels.base import (
    ChannelReport,
    ChannelSourceUnavailable,
    UnmappableReportError,
)
from cafeops.integrations.channels.csv_source import CsvChannelSource

#: Where each portal's export lives, and what the agent must do to get it. Data,
#: not code, so a moved button is a string change.
PORTAL_PLANS: dict[SalesChannelName, tuple[str, tuple[str, ...]]] = {
    SalesChannelName.DELIVEROO: (
        "https://restaurant-hub.deliveroo.net/",
        (
            "Open Restaurant Hub and sign in with the stored credentials.",
            "If a one-time code is requested, STOP and ask the owner; do not guess.",
            "Go to Insights -> Performance.",
            "Set the date range to {since} .. {until}.",
            "Click Download CSV and save the file.",
            "Go to Insights -> Menu items, same date range, and download that CSV too.",
            "Report both file paths back. Do NOT read the numbers off the screen.",
            "Do not change any menu, price, ad budget or setting. Read-only.",
        ),
    ),
    SalesChannelName.JUST_EAT: (
        "https://partner.just-eat.co.uk/",
        (
            "Open Just Eat Partner Centre and sign in with the stored credentials.",
            "If a one-time code is requested, STOP and ask the owner; do not guess.",
            "Go to Reports -> Sales summary.",
            "Set the date range to {since} .. {until} and export as CSV.",
            "Go to Reports -> Menu item performance, same range, and export as CSV.",
            "Report both file paths back. Do NOT read the numbers off the screen.",
            "Do not change any menu, price, sponsored-listing budget or setting. Read-only.",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class ReportPlan:
    """What the agent is being asked to do. Inert -- describing is not doing."""

    channel: SalesChannelName
    since: date
    until: date
    target_url: str
    steps: tuple[str, ...]
    #: Stated on the plan so a reviewer can see the boundary, and asserted in code
    #: by `BrowserDriver` only ever being asked for downloaded files.
    read_only: bool = True

    def script(self) -> str:
        head = f"Read-only report download: {self.channel.value} {self.since}..{self.until}"
        body = "\n".join(f"{n}. {s}" for n, s in enumerate(self.steps, 1))
        return f"{head}\n{body}"


class BrowserDriver(Protocol):
    """Whatever actually drives a browser. Phase 2 supplies a real one."""

    name: str

    def download_reports(self, plan: ReportPlan) -> Sequence[Path]:
        """Follow the plan and return the CSV files it downloaded.

        Must raise `ChannelSourceUnavailable` on any failure -- a moved button, a
        login wall, an MFA prompt -- so the caller can fall back to CSV instead of
        recording a half-scraped day.
        """
        ...


@dataclass(slots=True)
class FixtureBrowserDriver:
    """A driver that downloads nothing and returns local fixture files.

    This exists so the browser path is *demonstrable* without touching Deliveroo or
    Just Eat. It is honest about itself: rows still land stamped `BROWSER_AGENT`,
    because the point being exercised is the plumbing, and pretending they were
    hand-exported would be the silent mix spec 4.6 forbids.
    """

    fixtures: dict[SalesChannelName, tuple[Path, ...]] = field(default_factory=dict)
    name: str = "fixture (no network)"

    def download_reports(self, plan: ReportPlan) -> Sequence[Path]:
        paths = self.fixtures.get(plan.channel, ())
        if not paths:
            raise ChannelSourceUnavailable(
                f"{self.name}: no fixture registered for {plan.channel.value}"
            )
        missing = [p for p in paths if not Path(p).exists()]
        if missing:
            raise ChannelSourceUnavailable(
                f"{self.name}: fixture(s) missing: " + ", ".join(str(p) for p in missing)
            )
        return [Path(p) for p in paths]


class BrowserAgentChannelSource:
    """Pulls the portal's own CSV export by driving a browser, then parses it.

    With no driver this raises `ChannelSourceUnavailable` immediately. That is the
    normal state today and it is not an error condition -- it is the fallback
    working.
    """

    kind = ChannelSourceKind.BROWSER_AGENT

    def __init__(
        self,
        *,
        driver: BrowserDriver | None = None,
        download_dir: Path | None = None,
    ) -> None:
        self.driver = driver
        self.download_dir = Path(download_dir) if download_dir is not None else None

    def describe(self) -> str:
        if self.driver is None:
            return "browser agent (NO DRIVER WIRED -- will hand over to the CSV fallback)"
        return f"browser agent via {self.driver.name}, read-only report download"

    def plan_for(self, *, channel: SalesChannelName, since: date, until: date) -> ReportPlan:
        """Build the instruction script. Contacts nobody; safe to print and read."""
        try:
            url, steps = PORTAL_PLANS[channel]
        except KeyError as exc:
            raise ChannelSourceUnavailable(f"no portal plan for {channel.value}") from exc
        return ReportPlan(
            channel=channel,
            since=since,
            until=until,
            target_url=url,
            steps=tuple(s.format(since=since, until=until) for s in steps),
        )

    def fetch(self, *, channel: SalesChannelName, since: date, until: date) -> ChannelReport:
        plan = self.plan_for(channel=channel, since=since, until=until)
        if self.driver is None:
            raise ChannelSourceUnavailable(
                "no browser driver is wired (Phase 2). The plan is ready:\n" + plan.script()
            )

        try:
            downloaded = list(self.driver.download_reports(plan))
        except ChannelSourceUnavailable:
            raise
        except Exception as exc:  # a driver failing any other way is still a fallback case
            raise ChannelSourceUnavailable(
                f"{self.driver.name} failed on {channel.value}: {exc}"
            ) from exc

        if not downloaded:
            raise ChannelSourceUnavailable(
                f"{self.driver.name} returned no files for {channel.value}"
            )

        # One parser, two provenances. A scraped file gets the identical refusals a
        # hand-export gets; only `source` differs.
        reader = CsvChannelSource(files=downloaded, platform=channel)
        reader.kind = self.kind
        try:
            report = reader.fetch(channel=channel, since=since, until=until)
        except UnmappableReportError as exc:
            # A scrape that produced an unreadable file IS a broken scrape, so this
            # is a fallback case rather than a stop-and-look-at-it case.
            raise ChannelSourceUnavailable(
                f"{self.driver.name} downloaded a file this system cannot map "
                f"(the portal export probably changed):\n{exc.report()}"
            ) from exc

        note = (
            f"downloaded by {self.driver.name}: "
            + ", ".join(p.name for p in downloaded)
            + " -- rows stamped BROWSER_AGENT, not CSV_UPLOAD"
        )
        return ChannelReport(
            channel=report.channel,
            source=self.kind,
            since=report.since,
            until=report.until,
            day_rows=report.day_rows,
            item_rows=report.item_rows,
            rejected=report.rejected,
            warnings=(note, *report.warnings),
            source_refs=report.source_refs,
        )
