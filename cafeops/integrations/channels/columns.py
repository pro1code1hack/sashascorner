"""The column-mapping layer, and the reason it exists.

Deliveroo and Just Eat export the same *concepts* under different names, in
different orders, with money formatted differently and the item's size sometimes
folded into its name. Spec 4.6 and the brief both say: write a mapping layer, and
**reject a file you cannot map rather than guessing**.

The refusal is the design. Header matching is exact against a declared alias list
after normalisation -- no fuzzy matching, no "nearest column", no positional
fallback. A parser that guesses will one day read Just Eat's "Service fee" into
`ad_spend_pence`, and every ROAS figure afterwards is wrong with no symptom.

Four schemas are declared: daily and item-level, for each of the two platforms.
Adding a third platform is a new `ReportSchema`, not a new parser.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from cafeops.domain.types import SalesChannelName
from cafeops.integrations.channels.base import UnmappableReportError

#: Values that mean "the platform did not report this", distinct from zero.
BLANKS = frozenset({"", "-", "--", "n/a", "na", "null", "none", "not available"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# `\s` already covers the non-breaking and thin spaces some exports group
# thousands with, because Python matches Unicode whitespace by default.
_MONEY_STRIP = re.compile(r"[\s,]")
#: "Pistachio Latte (M)" -> ("Pistachio Latte", "M"). Just Eat folds size into the name.
_NAME_PAREN_SIZE = re.compile(r"^(?P<name>.+?)\s*\((?P<size>[A-Za-z]{1,3})\)\s*$")
#: `1.234,56` / `12,50` -- a comma used as the decimal point. See `_reject_decimal_comma`.
_DECIMAL_COMMA = re.compile(r",\d{2}\s*$")
#: Dates whose first two components are both <= 12, so DD/MM and MM/DD both parse and
#: the file cannot tell you which it meant. See `date_order_is_provable`.
_AMBIGUOUS_SLASH_DATE = re.compile(r"^(0?[1-9]|1[0-2])[/-](0?[1-9]|1[0-2])[/-]\d{2,4}$")

DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")


class FieldParseError(ValueError):
    """One cell could not be read. The row is rejected; the file is not."""


def normalise_header(raw: str) -> str:
    """`" Menu views "` -> `menu_views`. Also strips a BOM and a trailing unit hint."""
    text = raw.replace("﻿", "").strip().lower()
    return _NON_ALNUM.sub("_", text).strip("_")


def _is_blank(raw: str) -> bool:
    return raw.replace("﻿", "").strip().lower() in BLANKS


#: Currency marks a real export puts in front of a number. Legitimate in a money
#: column; in a count column they are the loudest possible sign that the header was
#: mapped to the wrong field.
_CURRENCY = "£$€"


def parse_int(raw: str) -> int | None:
    """A count: impressions, views, orders, a rank. Never money.

    A currency symbol here is a **refusal, not a strip**. `orders` reading `£18` means
    a money column was mapped onto a count, and quietly dropping the `£` would turn a
    column-mapping mistake into eighteen orders that nobody ever questions.
    """
    if _is_blank(raw):
        return None
    stripped = raw.strip()
    if any(mark in stripped for mark in _CURRENCY):
        raise FieldParseError(
            f"{raw!r} carries a currency symbol but this column is a count, not money -- "
            "the header is almost certainly mapped to the wrong field"
        )
    text = _MONEY_STRIP.sub("", stripped)
    _reject_decimal_comma(raw)
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise FieldParseError(f"{raw!r} is not a whole number") from exc
    if value != value.to_integral_value():
        raise FieldParseError(f"{raw!r} is not a whole number")
    return int(value)


def _reject_decimal_comma(raw: str) -> None:
    """Refuse `1.234,56` rather than reading it as 1.23456 and rounding it away.

    `_MONEY_STRIP` removes commas because English exports group thousands with them.
    A European export uses the comma as the *decimal* point and the dot as the
    grouping mark, so the same removal turns 1 234,56 into 1.23456 -- which
    `parse_money_pence` would then reject for sub-penny precision, but with a message
    about precision rather than about locale, sending the reader looking in the wrong
    place. Detected on the original text -- a comma followed by exactly two digits at
    the end -- because by the time `_MONEY_STRIP` has run the evidence is gone.
    """
    text = raw.strip()
    if _DECIMAL_COMMA.search(text):
        raise FieldParseError(
            f"{raw!r} looks like a European decimal comma (1.234,56). This parser reads "
            "English exports (1,234.56) -- re-export the file with a dot decimal point "
            "rather than have the value guessed at"
        )


def parse_money_pence(raw: str) -> int | None:
    """`"£1,234.56"` -> `123456`. Exact via Decimal; a float never touches money.

    More than two decimal places is a refusal rather than a rounding: a column the
    platform reports in some other unit is a mapping mistake, and quietly rounding
    it would hide that.
    """
    if _is_blank(raw):
        return None
    text = _MONEY_STRIP.sub("", raw.strip())
    _reject_decimal_comma(raw)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.lstrip(_CURRENCY)
    if text.startswith("-"):
        negative = True
        text = text[1:]
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise FieldParseError(f"{raw!r} is not a money amount") from exc
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise FieldParseError(f"{raw!r} has sub-penny precision; check the column mapping")
    pence = int(value * 100)
    return -pence if negative else pence


def parse_bp(raw: str) -> int | None:
    """A small ratio-like number to basis points: `"3.5"` -> `350`, `"4.62"` -> `462`.

    Average position and star rating both arrive as one decimal place. Stored as an
    integer so nothing downstream needs a float.
    """
    if _is_blank(raw):
        return None
    text = _MONEY_STRIP.sub("", raw.strip()).rstrip("%")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise FieldParseError(f"{raw!r} is not a decimal number") from exc
    return int((value * 100).to_integral_value(rounding="ROUND_HALF_UP"))


def parse_date(raw: str) -> date:
    """A calendar date in any of `DATE_FORMATS`. Mixed formats in one file are fine.

    `%d/%m/%Y` is tried before anything month-first, because the exports are British.
    A US-style `04/13/2026` therefore matches nothing and is refused with that named
    as the likely cause -- refusing is right, because reading it as 4 January 2013
    would put a day's revenue in the wrong year silently.
    """
    text = raw.replace("﻿", "").strip()
    if not text:
        raise FieldParseError("empty date")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007 -- a calendar date
        except ValueError:
            continue
    hint = ""
    if _looks_month_first(text):
        hint = (
            " -- the middle number is above 12, so this is probably MM/DD/YYYY. Re-export "
            "it as DD/MM/YYYY or YYYY-MM-DD; it is not being guessed at"
        )
    raise FieldParseError(f"{raw!r} matches none of {', '.join(DATE_FORMATS)}{hint}")


def _looks_month_first(text: str) -> bool:
    parts = re.split(r"[/-]", text)
    if len(parts) != 3:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return first <= 12 and second > 12


def date_order_is_provable(values: Sequence[str]) -> bool:
    """Does this set of slash-dates prove whether it is day-first or month-first?

    It does as soon as one value has a first component above 12. If every slash-date
    in a file is `<=12/<=12/YYYY`, both readings parse and the file is silently
    ambiguous -- a 6 April file read as 4 June, with no error anywhere. That cannot be
    refused (it would reject a legitimate British export of a quiet fortnight), so it
    is warned about instead, which is the honest position: the parser is assuming, and
    it says so.
    """
    slashed = [
        v.strip() for v in values if _AMBIGUOUS_SLASH_DATE.match(v.strip()) or "/" in v.strip()
    ]
    if not slashed:
        return True
    return any(not _AMBIGUOUS_SLASH_DATE.match(v) for v in slashed)


def parse_text(raw: str) -> str | None:
    text = raw.replace("﻿", "").strip()
    return text or None


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One canonical field and every header the platforms are known to call it."""

    field: str
    aliases: tuple[str, ...]
    parse: Callable[[str], object]
    required: bool = False

    def matches(self, normalised_header: str) -> bool:
        return normalised_header in self.aliases


@dataclass(frozen=True, slots=True)
class ReportSchema:
    """One platform's one report. Four of these cover spec 4.6 today."""

    platform: SalesChannelName
    kind: str  # "daily" | "item"
    label: str
    columns: tuple[ColumnSpec, ...]
    #: How the item's size arrives. "column" = its own column; "in_name" = "Foo (M)".
    size_style: str = "column"
    #: How many NON-required columns must map before this is accepted as the report.
    #:
    #: Load-bearing, and found by running it: a stray CSV whose first column happened
    #: to be called "Day" satisfied the required-column test on its own and was
    #: imported as a Deliveroo daily export, overwriting three real days with a row of
    #: NULLs. A date and nothing else is not a performance report. Three metrics is
    #: enough to identify a real export and cheap enough that no genuine one fails it.
    min_metric_columns: int = 3

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(c.field for c in self.columns if c.required)

    def spec_for(self, normalised_header: str) -> ColumnSpec | None:
        for column in self.columns:
            if column.matches(normalised_header):
                return column
        return None


def split_item_name(raw: str, *, size_style: str) -> tuple[str, str | None]:
    """Return (item name, size code or None) according to the platform's convention."""
    text = raw.strip()
    if size_style != "in_name":
        return text, None
    match = _NAME_PAREN_SIZE.match(text)
    if match is None:
        return text, None
    return match.group("name").strip(), match.group("size").strip().upper()


# ==========================================================================
# Deliveroo -- Restaurant Hub exports
# ==========================================================================

DELIVEROO_DAILY = ReportSchema(
    platform=SalesChannelName.DELIVEROO,
    kind="daily",
    label="Deliveroo Restaurant Hub -- daily performance",
    columns=(
        ColumnSpec("metric_date", ("date", "day"), parse_date, required=True),
        ColumnSpec("impressions", ("impressions", "ad_impressions"), parse_int),
        ColumnSpec("menu_views", ("menu_views", "menu_visits"), parse_int),
        ColumnSpec("conversions", ("conversions", "ad_conversions"), parse_int),
        ColumnSpec("orders", ("orders", "total_orders"), parse_int),
        ColumnSpec("ad_spend_pence", ("ad_spend", "ad_spend_gbp"), parse_money_pence),
        ColumnSpec(
            "attributed_revenue_pence",
            ("revenue_from_ads", "attributed_revenue", "ad_revenue"),
            parse_money_pence,
        ),
        ColumnSpec("commission_pence", ("commission", "commission_gbp"), parse_money_pence),
        ColumnSpec("gross_pence", ("gross_sales", "gross_sales_gbp"), parse_money_pence),
        ColumnSpec("avg_position_bp", ("average_position", "avg_position"), parse_bp),
        ColumnSpec("rating_bp", ("rating", "average_rating"), parse_bp),
    ),
)

DELIVEROO_ITEMS = ReportSchema(
    platform=SalesChannelName.DELIVEROO,
    kind="item",
    label="Deliveroo Restaurant Hub -- item performance",
    columns=(
        ColumnSpec("metric_date", ("date", "day"), parse_date, required=True),
        ColumnSpec("item_name", ("item_name", "item"), parse_text, required=True),
        ColumnSpec("size_code", ("size", "size_code", "variant"), parse_text),
        ColumnSpec("views", ("item_views", "views"), parse_int),
        ColumnSpec("orders", ("orders", "units_sold"), parse_int),
        ColumnSpec("revenue_pence", ("item_revenue", "revenue", "revenue_gbp"), parse_money_pence),
        ColumnSpec("rank_in_category", ("category_rank", "rank_in_category"), parse_int),
    ),
)

# ==========================================================================
# Just Eat -- Partner Centre exports. Different names for the same concepts, and
# the size folded into the item name.
# ==========================================================================

JUST_EAT_DAILY = ReportSchema(
    platform=SalesChannelName.JUST_EAT,
    kind="daily",
    label="Just Eat Partner Centre -- daily summary",
    columns=(
        ColumnSpec("metric_date", ("order_date", "business_date"), parse_date, required=True),
        ColumnSpec("impressions", ("listing_impressions", "impressions_sponsored"), parse_int),
        ColumnSpec("menu_views", ("menu_page_views", "views"), parse_int),
        ColumnSpec("conversions", ("sponsored_conversions",), parse_int),
        ColumnSpec("orders", ("order_count", "orders_delivered"), parse_int),
        ColumnSpec("ad_spend_pence", ("marketing_spend", "sponsored_spend"), parse_money_pence),
        ColumnSpec(
            "attributed_revenue_pence",
            ("sponsored_revenue", "revenue_attributed_to_sponsored"),
            parse_money_pence,
        ),
        ColumnSpec(
            "commission_pence",
            ("service_fee", "commission_charged"),
            parse_money_pence,
        ),
        ColumnSpec("gross_pence", ("order_value_total", "gross_order_value"), parse_money_pence),
        ColumnSpec("avg_position_bp", ("search_ranking", "average_ranking"), parse_bp),
        ColumnSpec("rating_bp", ("review_score", "rating_out_of_6"), parse_bp),
    ),
)

JUST_EAT_ITEMS = ReportSchema(
    platform=SalesChannelName.JUST_EAT,
    kind="item",
    label="Just Eat Partner Centre -- menu item report",
    columns=(
        ColumnSpec("metric_date", ("order_date", "business_date"), parse_date, required=True),
        ColumnSpec("item_name", ("menu_item", "product_name"), parse_text, required=True),
        ColumnSpec("views", ("item_impressions", "product_views"), parse_int),
        ColumnSpec("orders", ("quantity_sold", "units"), parse_int),
        ColumnSpec("revenue_pence", ("item_value", "product_revenue"), parse_money_pence),
        ColumnSpec("rank_in_category", ("position_in_category", "menu_position"), parse_int),
    ),
    size_style="in_name",
)

SCHEMAS: tuple[ReportSchema, ...] = (
    DELIVEROO_DAILY,
    DELIVEROO_ITEMS,
    JUST_EAT_DAILY,
    JUST_EAT_ITEMS,
)


def schema_for(platform: SalesChannelName, kind: str) -> ReportSchema:
    for schema in SCHEMAS:
        if schema.platform is platform and schema.kind == kind:
            return schema
    raise LookupError(f"no {kind!r} schema declared for {platform.value}")


@dataclass(frozen=True, slots=True)
class HeaderMapping:
    """The result of mapping one file's header row onto one schema."""

    schema: ReportSchema
    #: normalised header -> the spec it feeds. Only recognised headers appear.
    by_header: Mapping[str, ColumnSpec]
    #: Headers present in the file that no spec claims. A warning, not an error.
    unrecognised: tuple[str, ...]
    #: Original header text, in file order, for error messages.
    headers: tuple[str, ...]

    @property
    def mapped_fields(self) -> tuple[str, ...]:
        return tuple(sorted({spec.field for spec in self.by_header.values()}))


def map_headers(schema: ReportSchema, headers: Sequence[str]) -> HeaderMapping:
    """Map a header row onto `schema`, or refuse.

    Refusals, in the order they are checked:

    * a required field with no matching header -- the file is not this report;
    * two headers claiming the same field -- ambiguous, and picking one is guessing.
    """
    by_header: dict[str, ColumnSpec] = {}
    unrecognised: list[str] = []
    claimed: dict[str, str] = {}

    for raw in headers:
        normalised = normalise_header(raw)
        if not normalised:
            continue
        spec = schema.spec_for(normalised)
        if spec is None:
            unrecognised.append(raw.strip())
            continue
        if spec.field in claimed:
            raise UnmappableReportError(
                f"{schema.label}: columns {claimed[spec.field]!r} and {raw.strip()!r} both map "
                f"to {spec.field!r}; refusing to choose between them",
                headers=tuple(h.strip() for h in headers),
            )
        claimed[spec.field] = raw.strip()
        by_header[normalised] = spec

    missing = tuple(f for f in schema.required_fields if f not in claimed)
    if missing:
        raise UnmappableReportError(
            f"{schema.label}: required column(s) absent",
            headers=tuple(h.strip() for h in headers),
            missing=missing,
            unrecognised=tuple(unrecognised),
            candidates=(schema.label,),
        )

    mapped_fields = set(claimed)
    metrics = mapped_fields - set(schema.required_fields)
    if len(metrics) < schema.min_metric_columns:
        raise UnmappableReportError(
            f"{schema.label}: only {len(metrics)} metric column(s) recognised "
            f"({', '.join(sorted(metrics)) or 'none'}); at least "
            f"{schema.min_metric_columns} are needed before this is treated as that "
            "report. A date column and nothing else is not a performance export.",
            headers=tuple(h.strip() for h in headers),
            unrecognised=tuple(unrecognised),
            candidates=(schema.label,),
        )
    if len(unrecognised) > len(mapped_fields):
        raise UnmappableReportError(
            f"{schema.label}: {len(unrecognised)} column(s) unrecognised against "
            f"{len(mapped_fields)} mapped; most of this file is not understood, so it is "
            "not this export",
            headers=tuple(h.strip() for h in headers),
            unrecognised=tuple(unrecognised),
            candidates=(schema.label,),
        )
    return HeaderMapping(
        schema=schema,
        by_header=by_header,
        unrecognised=tuple(unrecognised),
        headers=tuple(h.strip() for h in headers),
    )


def detect_schema(headers: Sequence[str], *, kind: str | None = None) -> ReportSchema:
    """Work out which platform export this is, or refuse.

    Scored by how many headers a schema recognises, and only among schemas whose
    required columns are all present. A tie is a refusal: two schemas fitting
    equally well means the file is ambiguous, and spec 4.6's whole point is that a
    Deliveroo number and a Just Eat number must never be silently interchanged.
    """
    candidates = [s for s in SCHEMAS if kind is None or s.kind == kind]
    scored: list[tuple[int, ReportSchema]] = []
    for schema in candidates:
        try:
            mapping = map_headers(schema, headers)
        except UnmappableReportError:
            continue
        scored.append((len(mapping.by_header), schema))

    if not scored:
        raise UnmappableReportError(
            "no declared platform export matches this file's columns",
            headers=tuple(h.strip() for h in headers),
            candidates=tuple(s.label for s in candidates),
        )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best = scored[0]
    rivals = [s for score, s in scored[1:] if score == best[0]]
    if rivals:
        names = [best[1].label, *(s.label for s in rivals)]
        raise UnmappableReportError(
            "these columns fit more than one platform export equally well; "
            "pass the platform explicitly rather than letting it be guessed",
            headers=tuple(h.strip() for h in headers),
            candidates=tuple(names),
        )
    return best[1]
