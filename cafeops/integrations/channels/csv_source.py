"""`CsvChannelSource` -- the path that works today.

Spec 15 q6: the owner has portal logins and exports by hand. That makes this the
*primary* implementation, not a stopgap, and it is built accordingly:

* the platform is either declared or detected, never guessed (`columns.py`);
* a file whose columns do not map is **refused**, with a report naming what was
  missing and what was not recognised;
* a bad *row* is rejected and counted; a bad *file* stops the import. One
  unparseable cell on day 9 must not silently drop day 9 from a 14-day ROAS;
* a field the export omits stays `None`. Invariant 8, and the reason
  `ChannelMetric.net_pence` returns `None` rather than subtracting what it has;
* nothing is written. `fetch` returns an inert `ChannelReport`.

Duplicate dates inside one file are a refusal rather than a last-one-wins, because
`channel_metric` is unique on (channel, date) and a duplicated day means the export
covers two properties, two menus, or two windows stacked together.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from cafeops.domain.types import ChannelSourceKind, SalesChannelName
from cafeops.integrations.channels.base import (
    ChannelDayRow,
    ChannelItemRow,
    ChannelReport,
    RejectedRow,
    UnmappableReportError,
)
from cafeops.integrations.channels.columns import (
    FieldParseError,
    HeaderMapping,
    ReportSchema,
    detect_schema,
    map_headers,
    normalise_header,
    schema_for,
    split_item_name,
)

#: Which filename fragments hint at which platform and report. Used only to pick
#: the *candidate* schema for auto-discovery in a directory; the header row still
#: has the final say, so a misnamed file is refused rather than mis-parsed.
FILENAME_HINTS: tuple[tuple[str, SalesChannelName], ...] = (
    ("deliveroo", SalesChannelName.DELIVEROO),
    ("roo", SalesChannelName.DELIVEROO),
    ("just_eat", SalesChannelName.JUST_EAT),
    ("just-eat", SalesChannelName.JUST_EAT),
    ("justeat", SalesChannelName.JUST_EAT),
)


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """One file, read and mapped. `kind` comes from the schema, not the filename."""

    path: Path
    schema: ReportSchema
    mapping: HeaderMapping
    day_rows: tuple[ChannelDayRow, ...]
    item_rows: tuple[ChannelItemRow, ...]
    rejected: tuple[RejectedRow, ...]
    warnings: tuple[str, ...]


class CsvChannelSource:
    """Reads Deliveroo / Just Eat portal exports from disk.

    Either point it at explicit files, or at a directory it will scan. Files are
    matched to the requested channel by their *header row*: the filename is a hint
    for which schema to try first and nothing more.
    """

    kind = ChannelSourceKind.CSV_UPLOAD

    def __init__(
        self,
        *,
        files: Sequence[Path] | None = None,
        directory: Path | None = None,
        platform: SalesChannelName | None = None,
    ) -> None:
        if not files and directory is None:
            raise ValueError("CsvChannelSource needs `files` or a `directory`")
        self.files = tuple(Path(f) for f in (files or ()))
        self.directory = Path(directory) if directory is not None else None
        #: When set, the declared platform. Detection is skipped and a file whose
        #: columns belong to the other platform is refused rather than accepted.
        self.platform = platform

    def describe(self) -> str:
        where = ", ".join(str(f) for f in self.files) or f"{self.directory}/*.csv"
        declared = self.platform.value if self.platform else "auto-detected"
        return f"CSV portal export ({declared}) from {where}"

    # -- discovery ---------------------------------------------------------

    def _candidate_files(self, channel: SalesChannelName) -> list[Path]:
        if self.files:
            return list(self.files)
        assert self.directory is not None
        found: list[Path] = []
        for path in sorted(self.directory.glob("*.csv")):
            lowered = path.name.lower()
            hinted = {plat for frag, plat in FILENAME_HINTS if frag in lowered}
            # No hint at all means "try it" -- the header row decides. A hint for
            # the *other* platform means skip, so a Deliveroo file is never read
            # into a Just Eat sync just because it happened to parse.
            if hinted and channel not in hinted:
                continue
            found.append(path)
        return found

    # -- parsing -----------------------------------------------------------

    def parse_file(
        self, path: Path, *, platform: SalesChannelName | None = None, kind: str | None = None
    ) -> ParsedFile:
        """Read and map one file. Raises `UnmappableReportError` rather than guessing."""
        if not path.exists():
            raise UnmappableReportError(f"{path}: no such file")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration as exc:
                raise UnmappableReportError(f"{path}: file is empty") from exc
            body = [row for row in reader if any(cell.strip() for cell in row)]

        platform = platform or self.platform
        if platform is None:
            schema = detect_schema(headers, kind=kind)
        else:
            schema = self._schema_for_declared(headers, platform, kind)
        mapping = map_headers(schema, headers)

        if schema.kind == "daily":
            day_rows, rejected = self._read_day_rows(path, schema, mapping, headers, body)
            item_rows: tuple[ChannelItemRow, ...] = ()
        else:
            item_rows, rejected = self._read_item_rows(path, schema, mapping, headers, body)
            day_rows = ()

        warnings: list[str] = []
        if mapping.unrecognised:
            warnings.append(
                f"{path.name}: {len(mapping.unrecognised)} column(s) not recognised and ignored: "
                + ", ".join(mapping.unrecognised)
            )
        absent = [
            spec.field
            for spec in schema.columns
            if spec.field not in mapping.mapped_fields and not spec.required
        ]
        if absent:
            warnings.append(
                f"{path.name}: {schema.platform.value} export omits "
                + ", ".join(sorted(absent))
                + " -- those stay NULL, not zero"
            )
        return ParsedFile(
            path=path,
            schema=schema,
            mapping=mapping,
            day_rows=day_rows,
            item_rows=item_rows,
            rejected=rejected,
            warnings=tuple(warnings),
        )

    def _schema_for_declared(
        self, headers: Sequence[str], platform: SalesChannelName, kind: str | None
    ) -> ReportSchema:
        """Pick the declared platform's schema, choosing daily vs item by its columns."""
        if kind is not None:
            return schema_for(platform, kind)
        reasons: list[str] = []
        for candidate_kind in ("daily", "item"):
            schema = schema_for(platform, candidate_kind)
            try:
                map_headers(schema, headers)
            except UnmappableReportError as exc:
                # Keep every schema's own reason. "required but absent: item_name" on
                # its own is misleading when the daily schema failed for a different
                # reason entirely, and a misleading refusal gets overridden.
                reasons.append(str(exc))
                continue
            return schema
        raise UnmappableReportError(
            f"columns do not match either declared {platform.value} export:\n    "
            + "\n    ".join(reasons),
            headers=tuple(h.strip() for h in headers),
            candidates=tuple(schema_for(platform, k).label for k in ("daily", "item")),
        )

    def _read_day_rows(
        self,
        path: Path,
        schema: ReportSchema,
        mapping: HeaderMapping,
        headers: Sequence[str],
        body: Iterable[Sequence[str]],
    ) -> tuple[tuple[ChannelDayRow, ...], tuple[RejectedRow, ...]]:
        rows: list[ChannelDayRow] = []
        rejected: list[RejectedRow] = []
        seen: dict[date, int] = {}
        for offset, raw_row in enumerate(body, start=2):
            cells = dict(zip((normalise_header(h) for h in headers), raw_row, strict=False))
            try:
                values = self._parse_cells(mapping, cells)
            except FieldParseError as exc:
                rejected.append(
                    RejectedRow(line_no=offset, reason=str(exc), raw=self._raw(headers, raw_row))
                )
                continue
            metric_date = values["metric_date"]
            if not isinstance(metric_date, date):
                rejected.append(RejectedRow(line_no=offset, reason="no date on the row"))
                continue
            if metric_date in seen:
                raise UnmappableReportError(
                    f"{path.name}: {metric_date} appears on lines {seen[metric_date]} and "
                    f"{offset}. channel_metric is unique on (channel, date); refusing to pick "
                    "one. Split the export, or check it is not two properties stacked together."
                )
            seen[metric_date] = offset
            rows.append(
                ChannelDayRow(
                    channel=schema.platform,
                    metric_date=metric_date,
                    source=self.kind,
                    source_ref=path.name,
                    impressions=_as_int(values.get("impressions")),
                    menu_views=_as_int(values.get("menu_views")),
                    conversions=_as_int(values.get("conversions")),
                    orders=_as_int(values.get("orders")),
                    ad_spend_pence=_as_int(values.get("ad_spend_pence")),
                    attributed_revenue_pence=_as_int(values.get("attributed_revenue_pence")),
                    commission_pence=_as_int(values.get("commission_pence")),
                    gross_pence=_as_int(values.get("gross_pence")),
                    avg_position_bp=_as_int(values.get("avg_position_bp")),
                    rating_bp=_as_int(values.get("rating_bp")),
                )
            )
        return tuple(rows), tuple(rejected)

    def _read_item_rows(
        self,
        path: Path,
        schema: ReportSchema,
        mapping: HeaderMapping,
        headers: Sequence[str],
        body: Iterable[Sequence[str]],
    ) -> tuple[tuple[ChannelItemRow, ...], tuple[RejectedRow, ...]]:
        rows: list[ChannelItemRow] = []
        rejected: list[RejectedRow] = []
        seen: set[tuple[date, str, str | None]] = set()
        for offset, raw_row in enumerate(body, start=2):
            cells = dict(zip((normalise_header(h) for h in headers), raw_row, strict=False))
            try:
                values = self._parse_cells(mapping, cells)
            except FieldParseError as exc:
                rejected.append(
                    RejectedRow(line_no=offset, reason=str(exc), raw=self._raw(headers, raw_row))
                )
                continue
            metric_date = values["metric_date"]
            raw_name = values.get("item_name")
            if not isinstance(metric_date, date) or not isinstance(raw_name, str):
                rejected.append(
                    RejectedRow(
                        line_no=offset,
                        reason="row has no date or no item name",
                        raw=self._raw(headers, raw_row),
                    )
                )
                continue
            name, size_from_name = split_item_name(raw_name, size_style=schema.size_style)
            size_cell = values.get("size_code")
            size = (size_cell.strip().upper() if isinstance(size_cell, str) else None) or (
                size_from_name
            )
            key = (metric_date, name.lower(), size)
            if key in seen:
                raise UnmappableReportError(
                    f"{path.name}: line {offset} repeats {name!r} on {metric_date}. "
                    "channel_item_metric is unique on (channel, date, item); refusing to pick one."
                )
            seen.add(key)
            rows.append(
                ChannelItemRow(
                    channel=schema.platform,
                    metric_date=metric_date,
                    source=self.kind,
                    item_name=name,
                    size_code=size,
                    views=_as_int(values.get("views")),
                    orders=_as_int(values.get("orders")),
                    revenue_pence=_as_int(values.get("revenue_pence")),
                    rank_in_category=_as_int(values.get("rank_in_category")),
                )
            )
        return tuple(rows), tuple(rejected)

    @staticmethod
    def _parse_cells(mapping: HeaderMapping, cells: dict[str, str]) -> dict[str, object]:
        values: dict[str, object] = {}
        for header, spec in mapping.by_header.items():
            raw = cells.get(header, "")
            try:
                values[spec.field] = spec.parse(raw)
            except FieldParseError as exc:
                raise FieldParseError(f"column {header!r}: {exc}") from exc
        return values

    @staticmethod
    def _raw(headers: Sequence[str], row: Sequence[str]) -> dict[str, str]:
        return {h.strip(): v for h, v in zip(headers, row, strict=False)}

    # -- the protocol ------------------------------------------------------

    def fetch(self, *, channel: SalesChannelName, since: date, until: date) -> ChannelReport:
        if since > until:
            raise ValueError(f"since {since} is after until {until}")
        day_rows: list[ChannelDayRow] = []
        item_rows: list[ChannelItemRow] = []
        rejected: list[RejectedRow] = []
        warnings: list[str] = []
        refs: list[str] = []
        refused: list[str] = []

        candidates = self._candidate_files(channel)
        if not candidates:
            warnings.append(
                f"no candidate CSV found for {channel.value}"
                + (f" in {self.directory}" if self.directory else "")
            )

        for path in candidates:
            try:
                parsed = self.parse_file(path)
            except UnmappableReportError:
                # An explicitly named file is a hard refusal: the caller pointed at
                # it and must be told. A file merely sitting in the drop directory is
                # refused too, but loudly and per file, so one stray CSV cannot block
                # the exports either side of it. Neither path guesses.
                if self.files:
                    raise
                refused.append(path.name)
                continue
            if parsed.schema.platform is not channel:
                # Refuse rather than skip when the file was named explicitly: the
                # caller asked for this file and got the other platform's export,
                # which is exactly the silent mix spec 4.6 warns about.
                if self.files:
                    raise UnmappableReportError(
                        f"{path.name} is a {parsed.schema.platform.value} export but the sync "
                        f"asked for {channel.value}. Refusing to mix the two."
                    )
                continue
            refs.append(path.name)
            warnings.extend(parsed.warnings)
            rejected.extend(parsed.rejected)
            day_rows.extend(r for r in parsed.day_rows if since <= r.metric_date <= until)
            item_rows.extend(r for r in parsed.item_rows if since <= r.metric_date <= until)
            all_dates = [r.metric_date for r in parsed.day_rows]
            all_dates += [r.metric_date for r in parsed.item_rows]
            outside = [d for d in all_dates if not since <= d <= until]
            if outside:
                warnings.append(
                    f"{path.name}: {len(outside)} row(s) outside {since}..{until} were skipped "
                    f"({min(outside)}..{max(outside)})"
                )

        if refused:
            warnings.append(
                f"{len(refused)} file(s) in {self.directory} could not be mapped to a known "
                "platform export and were REFUSED, not guessed at: " + ", ".join(refused)
            )

        return ChannelReport(
            channel=channel,
            source=self.kind,
            since=since,
            until=until,
            day_rows=tuple(sorted(day_rows, key=lambda r: r.metric_date)),
            item_rows=tuple(sorted(item_rows, key=lambda r: (r.metric_date, r.item_name))),
            rejected=tuple(rejected),
            warnings=tuple(warnings),
            source_refs=tuple(refs),
        )

    def with_source_kind(self, kind: ChannelSourceKind) -> CsvChannelSource:
        """A copy stamping a different `source`, for a file typed by hand from a PDF.

        `MANUAL` and `CSV_UPLOAD` are different provenances even when the parsing is
        identical, and spec 4.6 wants them distinguishable after the fact.
        """
        clone = CsvChannelSource(
            files=self.files or None, directory=self.directory, platform=self.platform
        )
        clone.kind = kind
        return clone


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("a boolean is not a metric")
    if isinstance(value, int):
        return value
    raise TypeError(f"expected an int or None, got {type(value).__name__}")


def stamp_source(rows: Sequence[ChannelDayRow], *, source_ref: str) -> tuple[ChannelDayRow, ...]:
    """Re-stamp `source_ref` on a set of rows. Used by the browser-agent source."""
    return tuple(replace(row, source_ref=source_ref) for row in rows)
