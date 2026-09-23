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

Phase 3 hardening (`docs/phase3/agent-k-integrations.md` 3). A hand-made export is
messier than a fixture, and every item here is a way a real one differs:

* **The delimiter is detected, not assumed.** Excel on a machine with a comma decimal
  separator writes `;`; a copy out of Google Sheets often arrives tab-separated. The
  old reader assumed `,` and would have read such a file as a single column named
  `date_impressions_menu_views_...`, failed to map it, and blamed the columns. See
  `_sniff_delimiter`: the winner is the one that maps the most headers, and a tie is a
  refusal.
* **Row numbers are the file's, not the parser's.** Blank lines and skipped rows used
  to shift every subsequent number, so "row 14 rejected" pointed at row 12 -- and the
  brief's whole discipline is naming the column and the row.
* **A short or long row is rejected, not padded.** `zip(strict=False)` silently turned
  a truncated row into a row of NULLs, which is invariant 8 broken by accident: "the
  platform did not report this" and "the line was cut off" became the same thing.
* **A totals row is recognised.** Every portal puts one at the bottom. It used to land
  in the rejected pile with an unhelpful message about the date; now it is identified
  and skipped, so the rejected count means something again.
* **Day-first dates are assumed, and the assumption is stated.** A file whose slash
  dates are all `<=12/<=12` cannot prove its own order (`date_order_is_provable`).
  Refusing would reject a legitimate British export; assuming silently would move a
  day's revenue by months. So: assume, and warn.
"""

from __future__ import annotations

import csv
import io
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
    date_order_is_provable,
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
        headers, body, delimiter = _read_rows(path)
        platform = platform or self.platform
        if platform is None:
            schema = detect_schema(headers, kind=kind)
        else:
            schema = self._schema_for_declared(headers, platform, kind)
        mapping = map_headers(schema, headers)

        if schema.kind == "daily":
            day_rows, rejected, row_notes = self._read_day_rows(
                path, schema, mapping, headers, body
            )
            item_rows: tuple[ChannelItemRow, ...] = ()
        else:
            item_rows, rejected, row_notes = self._read_item_rows(
                path, schema, mapping, headers, body
            )
            day_rows = ()

        warnings: list[str] = list(row_notes)
        if delimiter != ",":
            warnings.append(
                f"{path.name}: delimiter detected as {_delimiter_name(delimiter)}, not a comma"
            )
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
        body: Iterable[_SourceRow],
    ) -> tuple[tuple[ChannelDayRow, ...], tuple[RejectedRow, ...], tuple[str, ...]]:
        rows: list[ChannelDayRow] = []
        rejected: list[RejectedRow] = []
        notes: list[str] = []
        date_cells: list[str] = []
        seen: dict[date, int] = {}
        for source in body:
            offset, raw_row = source.line_no, source.cells
            if _is_totals_row(headers, raw_row):
                notes.append(
                    f"{path.name} line {offset}: a totals/subtotal row, skipped. It is not a "
                    "day, and summing an export's own total into the days would double the "
                    "window."
                )
                continue
            shape = _shape_problem(headers, raw_row)
            if shape is not None:
                rejected.append(
                    RejectedRow(
                        line_no=offset,
                        reason=f"{path.name} line {offset}: {shape}",
                        raw=self._raw(headers, raw_row),
                    )
                )
                continue
            cells = dict(zip((normalise_header(h) for h in headers), raw_row, strict=True))
            try:
                values = self._parse_cells(mapping, cells)
            except FieldParseError as exc:
                rejected.append(
                    RejectedRow(
                        line_no=offset,
                        reason=f"{path.name} line {offset}: {exc}",
                        raw=self._raw(headers, raw_row),
                    )
                )
                continue
            metric_date = values["metric_date"]
            if not isinstance(metric_date, date):
                rejected.append(
                    RejectedRow(line_no=offset, reason=f"{path.name} line {offset}: no date")
                )
                continue
            date_cells.append(_date_cell(mapping, cells))
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
        notes.extend(_date_order_notes(path, date_cells))
        return tuple(rows), tuple(rejected), tuple(notes)

    def _read_item_rows(
        self,
        path: Path,
        schema: ReportSchema,
        mapping: HeaderMapping,
        headers: Sequence[str],
        body: Iterable[_SourceRow],
    ) -> tuple[tuple[ChannelItemRow, ...], tuple[RejectedRow, ...], tuple[str, ...]]:
        rows: list[ChannelItemRow] = []
        rejected: list[RejectedRow] = []
        notes: list[str] = []
        date_cells: list[str] = []
        seen: set[tuple[date, str, str | None]] = set()
        for source in body:
            offset, raw_row = source.line_no, source.cells
            if _is_totals_row(headers, raw_row):
                notes.append(f"{path.name} line {offset}: a totals/subtotal row, skipped")
                continue
            shape = _shape_problem(headers, raw_row)
            if shape is not None:
                rejected.append(
                    RejectedRow(
                        line_no=offset,
                        reason=f"{path.name} line {offset}: {shape}",
                        raw=self._raw(headers, raw_row),
                    )
                )
                continue
            cells = dict(zip((normalise_header(h) for h in headers), raw_row, strict=True))
            try:
                values = self._parse_cells(mapping, cells)
            except FieldParseError as exc:
                rejected.append(
                    RejectedRow(
                        line_no=offset,
                        reason=f"{path.name} line {offset}: {exc}",
                        raw=self._raw(headers, raw_row),
                    )
                )
                continue
            metric_date = values["metric_date"]
            raw_name = values.get("item_name")
            if not isinstance(metric_date, date) or not isinstance(raw_name, str):
                rejected.append(
                    RejectedRow(
                        line_no=offset,
                        reason=f"{path.name} line {offset}: no date or no item name",
                        raw=self._raw(headers, raw_row),
                    )
                )
                continue
            date_cells.append(_date_cell(mapping, cells))
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
        notes.extend(_date_order_notes(path, date_cells))
        return tuple(rows), tuple(rejected), tuple(notes)

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


# ==========================================================================
# Reading a file somebody exported by hand
# ==========================================================================


@dataclass(frozen=True, slots=True)
class _SourceRow:
    """One body row, carrying the line number it actually occupies in the file.

    The number is load-bearing. `enumerate` over a filtered list drifts by one for
    every blank line above it, and a refusal that names the wrong row is worse than a
    refusal that names none: somebody opens the file, finds a perfectly good row at
    line 14, and concludes the parser is broken.
    """

    line_no: int
    cells: tuple[str, ...]


#: Delimiters a hand-made export actually arrives with. `;` comes from Excel on a
#: machine whose locale uses the comma as a decimal separator -- the single commonest
#: way a European-configured spreadsheet writes "CSV". Tab comes from a copy-paste out
#: of a web table or Google Sheets. `|` is rare but unambiguous when present.
_DELIMITERS: tuple[str, ...] = (",", ";", "\t", "|")

_DELIMITER_NAMES: dict[str, str] = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}

#: Words a portal puts in the first column of its summary row.
_TOTALS_WORDS = frozenset(
    {"total", "totals", "grand total", "subtotal", "sub total", "sum", "all", "overall", "average"}
)


def _delimiter_name(delimiter: str) -> str:
    return _DELIMITER_NAMES.get(delimiter, repr(delimiter))


def _read_rows(path: Path) -> tuple[tuple[str, ...], list[_SourceRow], str]:
    """Header, body rows with true line numbers, and the delimiter that was used.

    `utf-8-sig` eats a BOM; `newline=""` hands CRLF to the csv module, which is what
    it wants. Blank rows are dropped here -- but only after their line number has been
    recorded on the rows around them.
    """
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise UnmappableReportError(f"{path}: file is empty")
    delimiter = _sniff_delimiter(path, text)
    # Read through a StringIO with `newline=""` rather than over `splitlines()`: the csv
    # module then owns line endings (CRLF included) and, more importantly, keeps a
    # quoted field that contains a newline as one field. `reader.line_num` is the file's
    # own line counter, which is the number a refusal must quote. For a record that
    # spans lines it is the record's LAST line -- close enough to find it, and the only
    # number csv offers.
    handle = io.StringIO(text, newline="")
    reader = csv.reader(handle, delimiter=delimiter)
    headers: tuple[str, ...] | None = None
    body: list[_SourceRow] = []
    for row in reader:
        cells = tuple(row)
        if not any(cell.strip() for cell in cells):
            continue
        if headers is None:
            headers = cells
            continue
        body.append(_SourceRow(line_no=reader.line_num, cells=cells))
    if headers is None:
        raise UnmappableReportError(f"{path}: file has no non-blank row")
    return headers, body, delimiter


def _sniff_delimiter(path: Path, text: str) -> str:
    """Pick the delimiter that yields the most *recognised* headers, or refuse.

    Deliberately not `csv.Sniffer`: it guesses from character frequency, so a comma
    file whose values are quoted thousands (`"1,234.56"`) can out-score its own real
    delimiter. Scoring by how many headers a declared schema actually claims is both
    stricter and easier to explain -- the file is asked which of our four exports it
    is, and only a delimiter that makes it look like one of them can win.

    Falls back to the delimiter that produces the most columns when nothing maps,
    because a file that maps under no delimiter must still reach `map_headers` and be
    refused *there*, with its column names in the message. Refusing here would report
    a delimiter problem for what is really an unknown export.
    """
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    scores: list[tuple[int, int, str]] = []
    for delimiter in _DELIMITERS:
        headers = next(iter(csv.reader([first_line], delimiter=delimiter)), [])
        if len(headers) < 2:
            continue
        recognised = sum(
            1
            for schema in _candidate_schemas()
            for header in headers
            if schema.spec_for(normalise_header(header)) is not None
        )
        scores.append((recognised, len(headers), delimiter))
    if not scores:
        return ","
    scores.sort(reverse=True)
    best = scores[0]
    if best[0] == 0:
        # Nothing recognised under any delimiter. Take the widest split so the header
        # names in the eventual refusal are readable, and let `map_headers` say no.
        return max(scores, key=lambda s: s[1])[2]
    rivals = [s for s in scores[1:] if s[0] == best[0]]
    if rivals:
        names = ", ".join(_delimiter_name(s[2]) for s in (best, *rivals))
        raise UnmappableReportError(
            f"{path.name}: the header row maps equally well split by {names}. Refusing to "
            "choose -- a wrong delimiter silently shifts every value one column left, which "
            "is exactly how ad spend ends up in the commission column."
        )
    return best[2]


def _candidate_schemas() -> tuple[ReportSchema, ...]:
    from cafeops.integrations.channels.columns import SCHEMAS

    return SCHEMAS


def _is_totals_row(headers: Sequence[str], row: Sequence[str]) -> bool:
    """Is this the summary row every portal puts at the bottom?

    Two signatures, and both are needed:

    * a totals word in any of the first two cells ("Total", "Grand total");
    * a row whose leading cell(s) are blank but which still carries numbers -- how a
      spreadsheet writes a totals row when the date column simply has nothing in it.

    Recognising it matters because it used to arrive as a rejected row complaining
    about a date, which made the rejected count mean two different things at once.
    """
    filled = [cell.strip() for cell in row]
    leading = [cell.lower() for cell in filled[:2]]
    if any(cell in _TOTALS_WORDS for cell in leading):
        return True
    if any(cell.lower().startswith(("total", "grand total", "subtotal")) for cell in filled[:2]):
        return True
    if len(row) == len(headers) and not filled[0]:
        # Blank first column, but numbers further along: a summary line, not a day.
        return any(_looks_numeric(cell) for cell in filled[1:])
    return False


def _looks_numeric(cell: str) -> bool:
    stripped = cell.strip().lstrip("£$€").replace(",", "").replace(" ", "")
    if not stripped:
        return False
    try:
        float(stripped)
    except ValueError:
        return False
    return True


def _shape_problem(headers: Sequence[str], row: Sequence[str]) -> str | None:
    """Refuse a row whose cell count does not match the header.

    `zip(..., strict=False)` used to pad a truncated row out with absent keys, which
    `_parse_cells` then read as blanks and stored as `None` -- so "the platform did not
    report this" (invariant 8) and "this line was cut off mid-write" became
    indistinguishable. They are not the same statement and must not share a
    representation.
    """
    if len(row) == len(headers):
        return None
    trailing_blanks = len(row) - len(headers)
    if trailing_blanks > 0 and not any(cell.strip() for cell in row[len(headers) :]):
        # Trailing empty cells only -- a spreadsheet artefact, not lost data.
        return None
    return (
        f"row has {len(row)} cell(s) against {len(headers)} header(s) "
        f"({'truncated' if len(row) < len(headers) else 'over-long'}); "
        "not padded, because a missing cell and an unreported figure are different things"
    )


def _date_cell(mapping: HeaderMapping, cells: dict[str, str]) -> str:
    for header, spec in mapping.by_header.items():
        if spec.field == "metric_date":
            return cells.get(header, "")
    return ""


def _date_order_notes(path: Path, date_cells: Sequence[str]) -> list[str]:
    """Warn when a file's slash-dates cannot prove their own day/month order."""
    if not date_cells or date_order_is_provable(date_cells):
        return []
    return [
        f"{path.name}: every slash-separated date in this file has both its first two numbers "
        "at 12 or below, so the file cannot prove whether it is DD/MM or MM/DD. They were read "
        "as DD/MM/YYYY (British). If the export is American, these days are wrong by months -- "
        "re-export as YYYY-MM-DD and this warning goes away."
    ]


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
