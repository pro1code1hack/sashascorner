"""`CsvPaymentSource` -- read a back-office payment export.

The reader borrows the hardening `integrations/channels` already earned against real
hand-made exports, because a payment report arrives the same way and is messier than
a fixture: `normalise_header`, `parse_money_pence`, `parse_date`, `parse_int` and the
blank-vs-zero rule all come from `channels/columns.py` rather than being written twice.

What this adds is the refusal discipline, applied to money:

* a file whose columns do not map is **refused** and named -- never positionally
  guessed, because reading a "Service charge" column into `fees_pence` would make
  every net figure wrong with no symptom;
* a bad *row* is rejected and counted; a bad *file* stops the import;
* a field the export omits stays `None`, so "not reported" and "zero" never merge;
* a duplicated (date, method) inside one file is a refusal, not last-one-wins: it
  means two tills, two properties or two windows stacked together, and silently
  keeping one would halve the day's takings.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cafeops.config import settings
from cafeops.db.models.enums import PaymentMethod, PaymentSourceKind
from cafeops.integrations.channels.columns import (
    FieldParseError,
    normalise_header,
    parse_date,
    parse_int,
    parse_money_pence,
)
from cafeops.integrations.payments.base import (
    PaymentDayRow,
    PaymentReport,
    PaymentSourceUnavailable,
)

#: Header aliases, written naturally and normalised at import so the spellings here
#: and the spellings in a real export are compared the same way. `normalise_header`
#: lowercases and underscores, so "Payment method" becomes "payment_method" -- writing
#: the aliases pre-normalised by hand is how a mapper silently stops matching.
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "business_date": ("date", "business date", "day", "trading date"),
    "method": ("method", "payment method", "payment type", "tender", "type"),
    "gross_pence": ("gross", "gross sales", "amount", "total", "takings", "gross amount"),
    "refunds_pence": ("refunds", "refunded", "refund amount", "returns"),
    "fees_pence": ("fees", "fee", "processing fees", "card fees", "merchant fees"),
    "discounts_pence": ("discounts", "discount", "comps", "promotions"),
    "transactions": ("transactions", "count", "txns", "num transactions", "orders"),
}

ALIASES: dict[str, frozenset[str]] = {
    field: frozenset(normalise_header(a) for a in names) for field, names in _RAW_ALIASES.items()
}

REQUIRED = ("business_date", "method", "gross_pence")

#: How a report's own words map onto `PaymentMethod`. Anything unrecognised becomes
#: OTHER *and is named in the notes* -- kept and labelled, never folded into CARD.
_RAW_METHOD_WORDS: dict[str, PaymentMethod] = {
    "cash": PaymentMethod.CASH,
    "card": PaymentMethod.CARD,
    "credit": PaymentMethod.CARD,
    "debit": PaymentMethod.CARD,
    "credit card": PaymentMethod.CARD,
    "debit card": PaymentMethod.CARD,
    "contactless": PaymentMethod.CARD,
    "visa": PaymentMethod.CARD,
    "mastercard": PaymentMethod.CARD,
    "amex": PaymentMethod.CARD,
    "voucher": PaymentMethod.VOUCHER,
    "gift card": PaymentMethod.VOUCHER,
    "gift": PaymentMethod.VOUCHER,
    "account": PaymentMethod.ACCOUNT,
    "on account": PaymentMethod.ACCOUNT,
}

METHOD_WORDS: dict[str, PaymentMethod] = {
    normalise_header(k): v for k, v in _RAW_METHOD_WORDS.items()
}


def payments_dir() -> Path:
    """Where exports are looked for. `CAFEOPS_PAYMENTS_CSV_DIR`, or the fixtures."""
    raw = getattr(settings, "payments_csv_dir", None)
    if raw:
        return Path(str(raw))
    return Path(__file__).parent / "fixtures"


@dataclass(frozen=True, slots=True)
class _Row:
    number: int
    cells: tuple[str, ...]


def _sniff(text: str) -> str:
    """The delimiter that maps the most headers. A tie is a refusal.

    Excel on a comma-decimal machine writes `;`; a paste out of Sheets is often
    tab-separated. Assuming `,` reads such a file as one giant column and then
    blames the columns.
    """
    first = text.splitlines()[0] if text.splitlines() else ""
    best: list[tuple[int, str]] = []
    for delim in (",", ";", "\t", "|"):
        headers = [normalise_header(h) for h in first.split(delim)]
        mapped = sum(1 for field, names in ALIASES.items() if any(h in names for h in headers))
        best.append((mapped, delim))
    best.sort(key=lambda x: -x[0])
    # Order matters. A file with NO recognisable columns maps zero under every
    # delimiter, which is technically a tie -- reporting it as "cannot tell the
    # delimiter apart" sends the reader to re-save a file whose real problem is that
    # none of its headers were understood.
    if best[0][0] == 0:
        raise FieldParseError(
            "no recognised columns under any delimiter. Expected a date, a payment "
            f"method and an amount; the known names are {_known_columns()}"
        )
    if len(best) > 1 and best[0][0] == best[1][0]:
        raise FieldParseError(
            f"cannot tell the delimiter apart: {best[0][1]!r} and {best[1][1]!r} map "
            "the same number of columns. Save the export as a plain comma CSV."
        )
    return best[0][1]


def _known_columns() -> str:
    """The names the mapper accepts, so a refusal is actionable."""
    return "; ".join(
        f"{field} = {', '.join(sorted(names))}" for field, names in sorted(_RAW_ALIASES.items())
    )


def _map_headers(headers: Sequence[str]) -> dict[str, int]:
    norm = [normalise_header(h) for h in headers]
    out: dict[str, int] = {}
    for field, names in ALIASES.items():
        for i, h in enumerate(norm):
            if h in names:
                out[field] = i
                break
    return out


def _method(raw: str, notes: list[str]) -> PaymentMethod:
    key = normalise_header(raw)
    if key in METHOD_WORDS:
        return METHOD_WORDS[key]
    note = f"payment method {raw!r} is not recognised; kept as OTHER rather than guessed"
    if note not in notes:
        notes.append(note)
    return PaymentMethod.OTHER


class CsvPaymentSource:
    """Read every mappable payment export in a directory."""

    kind = PaymentSourceKind.CSV_UPLOAD

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or payments_dir()

    def fetch(self, *, since: date, until: date) -> PaymentReport:
        if not self.directory.exists():
            raise PaymentSourceUnavailable(
                f"no payment export directory at {self.directory}. Set "
                "CAFEOPS_PAYMENTS_CSV_DIR to where the back office writes them."
            )
        files = sorted(p for p in self.directory.glob("*.csv") if p.is_file())
        if not files:
            raise PaymentSourceUnavailable(f"no .csv files in {self.directory}")

        rows: list[PaymentDayRow] = []
        rejected: list[str] = []
        unmapped: list[str] = []
        notes: list[str] = []
        seen: set[tuple[date, PaymentMethod]] = set()

        for path in files:
            try:
                parsed = self._read(path, since=since, until=until, notes=notes)
            except FieldParseError as exc:
                unmapped.append(f"{path.name}: {exc}")
                continue
            for row, why in parsed:
                if why is not None:
                    rejected.append(why)
                    continue
                assert row is not None
                key = (row.business_date, row.method)
                if key in seen:
                    raise FieldParseError(
                        f"{path.name}: {row.business_date} {row.method.value} appears "
                        "twice. Two tills or two windows stacked in one file would "
                        "halve the day if one were dropped, so this is refused."
                    )
                seen.add(key)
                rows.append(row)

        rows.sort(key=lambda r: (r.business_date, r.method.value))
        return PaymentReport(
            kind=self.kind,
            since=since,
            until=until,
            rows=tuple(rows),
            rejected=tuple(rejected),
            unmapped=tuple(unmapped),
            notes=tuple(notes),
        )

    def _read(
        self, path: Path, *, since: date, until: date, notes: list[str]
    ) -> Iterable[tuple[PaymentDayRow | None, str | None]]:
        text = path.read_text(encoding="utf-8-sig")
        delim = _sniff(text)
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        raw = [_Row(number=i + 1, cells=tuple(c.strip() for c in r)) for i, r in enumerate(reader)]
        raw = [r for r in raw if any(c for c in r.cells)]
        if not raw:
            raise FieldParseError("the file is empty")

        headers = raw[0].cells
        mapping = _map_headers(headers)
        missing = [f for f in REQUIRED if f not in mapping]
        if missing:
            raise FieldParseError(
                f"missing required column(s) {', '.join(missing)}; saw "
                f"{', '.join(headers)}. Columns are matched by name, never by position."
            )

        out: list[tuple[PaymentDayRow | None, str | None]] = []
        skipped_window = 0
        for r in raw[1:]:
            if len(r.cells) != len(headers):
                out.append(
                    (
                        None,
                        f"{path.name} row {r.number}: {len(r.cells)} cells against "
                        f"{len(headers)} headers -- a cut-off row is rejected, not padded",
                    )
                )
                continue
            try:
                when = parse_date(r.cells[mapping["business_date"]])
            except FieldParseError as exc:
                out.append((None, f"{path.name} row {r.number}: {exc}"))
                continue
            if when < since or when > until:
                skipped_window += 1
                continue
            try:
                gross = parse_money_pence(r.cells[mapping["gross_pence"]])
                if gross is None:
                    out.append((None, f"{path.name} row {r.number}: no gross amount reported"))
                    continue
                row = PaymentDayRow(
                    business_date=when,
                    method=_method(r.cells[mapping["method"]], notes),
                    gross_pence=gross,
                    refunds_pence=_opt_money(r, mapping, "refunds_pence"),
                    fees_pence=_opt_money(r, mapping, "fees_pence"),
                    discounts_pence=_opt_money(r, mapping, "discounts_pence"),
                    transactions=_opt_int(r, mapping, "transactions"),
                    source_ref=path.name,
                )
            except FieldParseError as exc:
                out.append((None, f"{path.name} row {r.number}: {exc}"))
                continue
            out.append((row, None))

        if skipped_window:
            notes.append(
                f"{path.name}: {skipped_window} row(s) outside {since}..{until} were skipped"
            )
        return out


def _opt_money(row: _Row, mapping: dict[str, int], field: str) -> int | None:
    if field not in mapping:
        return None
    return parse_money_pence(row.cells[mapping[field]])


def _opt_int(row: _Row, mapping: dict[str, int], field: str) -> int | None:
    if field not in mapping:
        return None
    return parse_int(row.cells[mapping[field]])


__all__ = ["ALIASES", "CsvPaymentSource", "payments_dir"]
