"""Does modifier traffic reach us at all? A read-only probe over ingested sales.

ARCHITECTURE.md §8K decided **not** to build the real-time modifier stream. That
decision rests on one empirical question nothing in this repository can answer --
**how does the till actually ring an alt milk?** -- and §8K.4 showed why: the only
evidence we hold is one sentence in a spreadsheet, and there is no item-level sales
history anywhere in the project to check it against.

So this module is the instrument that answers it on the first real sync, from the
financial feed alone. It reads `sale`; it writes nothing, ever.

Three signals, in descending order of usefulness:

1. **Proxy lines.** How many times a delta-recipe item (`Oat milk (+upcharge)`) was
   rung as its own line. Non-zero means §8K.3's substitute is live and oat milk can
   be calculated -- and promoted -- with no new software.
2. **Modifier-carrying lines.** How many ingested lines arrived with any modifier
   attached. Expected to be zero on a nightly financial sync (§11 item 2); if it is
   *not* zero, the merchant's feed carries more than the public schema documents and
   §8K should be revisited.
3. **Price residue.** A line whose gross exceeds `menu_item.price_pence x qty` is the
   **footprint of a modifier the financial endpoint did not show us** -- the money
   survives even when the modifier does not. This is evidence, not a measurement, and
   the module is built so it cannot be mistaken for one: every residue is reported
   with *all* the modifiers whose price could explain it, and an alt milk (40p) is
   indistinguishable from a syrup add-on (40p). **Nothing here may drive depletion.**

Two things that also produce a residue, named so nobody reads one as proof of a
modifier: a sell price edited since the sale was rung (`menu_item.price_pence` is
single-valued -- there is no sell-price history in this schema), and any discount or
meal-deal adjustment the till applied to the line. Both are called out in
`ResidueReport.caveats`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import ManualRecipeLine, MenuItem, Modifier, Sale, VariantOption

__all__ = ["ModifierAudit", "ProxyItemUse", "ResidueGroup", "audit_modifiers"]


@dataclass(frozen=True, slots=True)
class ProxyItemUse:
    """One delta-recipe item, and how often the till rang it in the window."""

    menu_item_id: int
    name: str
    lightspeed_id: str | None
    adds_ingredient_id: int
    adds_ingredient: str
    adds_qty: Decimal
    credits_ingredient: str | None
    credits_qty: Decimal | None
    lines_rung: int
    units_rung: Decimal

    def sentence(self) -> str:
        credit = (
            f", crediting {self.credits_qty} of {self.credits_ingredient}"
            if self.credits_ingredient is not None
            else ""
        )
        return (
            f"{self.name!r} (menu_item {self.menu_item_id}): rung {self.lines_rung} time(s), "
            f"{self.units_rung} unit(s); each adds {self.adds_qty} of "
            f"{self.adds_ingredient}{credit}"
        )


@dataclass(frozen=True, slots=True)
class ResidueGroup:
    """Lines sharing one unexplained per-unit surcharge."""

    residue_pence: int
    lines: int
    #: Every modifier and variant option priced at exactly this residue. More than one
    #: means the residue cannot identify an ingredient, which is the normal case.
    explanations: tuple[str, ...]
    examples: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.explanations) != 1

    def sentence(self) -> str:
        if not self.explanations:
            what = "nothing priced at this amount -- a price edit or a discount, not a modifier"
        elif self.ambiguous:
            what = "AMBIGUOUS between " + ", ".join(self.explanations)
        else:
            what = f"consistent with {self.explanations[0]}"
        return f"+{self.residue_pence}p on {self.lines} line(s): {what}"


#: Receipt-id prefix `seed/demo.py` stamps on synthetic sales. A string dependency,
#: and a knowingly bad one (ARCHITECTURE.md 8I): `sale` has no provenance column, so
#: there is no structural way to tell a seeded line from one a till really produced.
#: It matters here more than anywhere, because the whole point of this probe is to say
#: what the REAL feed carries, and answering that from invented rows is worse than not
#: answering. **Requested of whoever owns the sale model: a `sale.source` enum, the
#: way `channel_metric.source` already does it.** Until then, this prefix, labelled as
#: a guess everywhere it is used.
SEEDED_RECEIPT_PREFIX = "DEMO-"


@dataclass(slots=True)
class ModifierAudit:
    since: date
    until: date
    lines_considered: int = 0
    lines_with_modifiers: int = 0
    #: Lines whose receipt id carries `SEEDED_RECEIPT_PREFIX`. See its comment: this is
    #: a heuristic standing in for a provenance column the schema does not have.
    likely_seeded_lines: int = 0
    seeded_lines_with_modifiers: int = 0
    proxy_use: tuple[ProxyItemUse, ...] = ()
    residues: tuple[ResidueGroup, ...] = ()
    lines_priced_below_list: int = 0
    caveats: list[str] = field(default_factory=list)

    @property
    def real_lines(self) -> int:
        return self.lines_considered - self.likely_seeded_lines

    @property
    def real_lines_with_modifiers(self) -> int:
        return self.lines_with_modifiers - self.seeded_lines_with_modifiers

    @property
    def proxy_lines(self) -> int:
        return sum(p.lines_rung for p in self.proxy_use)

    @property
    def residue_lines(self) -> int:
        return sum(g.lines for g in self.residues)

    def verdict(self) -> str:
        """The one sentence someone reading this wants. Deliberately unhedged.

        Read off the lines that did NOT come from `seed --demo`: a seeded row carries
        whatever modifier the seed felt like inventing, and letting those decide the
        verdict would have this probe confidently answer its own question with fiction.
        """
        if self.real_lines == 0:
            return (
                "Nothing but seeded sales in this window, so there is nothing to conclude. "
                "The probe needs a real `cafeops sync` before it can say anything."
            )
        if self.proxy_lines and self.real_lines_with_modifiers:
            return (
                "BOTH paths are in use: the till rings the upcharge item AND attaches "
                "modifiers. Fix the till layout before trusting either -- see the "
                "DOUBLE-COUNT RISK lines from `cafeops sync` (ARCHITECTURE.md 8K.3)."
            )
        if self.proxy_lines:
            return (
                f"The upcharge/add-on items ARE rung ({self.proxy_lines} line(s)). The "
                "substitute works: alt-milk consumption is being calculated from the "
                "ordinary sales feed, and those ingredients can earn tier A on their "
                "drift like anything else."
            )
        if self.real_lines_with_modifiers:
            return (
                f"{self.real_lines_with_modifiers} non-seeded line(s) carried modifiers -- "
                "more than the "
                "documented financial schema promises (ARCHITECTURE.md 11 item 2). Worth "
                "re-reading 8K: if this merchant's feed really carries modifiers, the "
                "whole stream question changes."
            )
        if self.residue_lines:
            return (
                f"No modifier data and no upcharge lines, but {self.residue_lines} line(s) "
                "were rung ABOVE list price. That is the footprint of modifiers we cannot "
                "see. Alt-milk consumption is invisible, and whole milk is being "
                "over-depleted for every one of them (8K.2)."
            )
        return (
            "No modifier data, no upcharge lines, no price residue. Either no modifiers "
            "are being sold, or they are free -- and the first is worth confirming with "
            "the owner before concluding anything, because the alternative is that alt "
            "milk is rung as a plain drink and is invisible in every direction."
        )


def audit_modifiers(session: Session, *, since: date, until: date) -> ModifierAudit:
    """Read `sale` over [since, until] local days and report the three signals."""
    audit = ModifierAudit(since=since, until=until)
    proxy_items = _proxy_items(session)
    price_index = _price_index(session)

    rows = session.execute(
        select(
            Sale.id,
            Sale.lightspeed_receipt_id,
            Sale.lightspeed_line_id,
            Sale.menu_item_id,
            Sale.qty,
            Sale.gross_pence,
            Sale.sold_at,
            Sale.applied_modifiers,
            MenuItem.name,
            MenuItem.price_pence,
        )
        .join(MenuItem, MenuItem.id == Sale.menu_item_id)
        .where(Sale.voided.is_(False), Sale.is_refund.is_(False))
    ).all()

    by_proxy: dict[int, tuple[int, Decimal]] = {}
    residue_lines: dict[int, list[str]] = {}
    below = 0

    for row in rows:
        if not _in_window(row.sold_at, since, until):
            continue
        audit.lines_considered += 1
        seeded = row.lightspeed_receipt_id.startswith(SEEDED_RECEIPT_PREFIX)
        if seeded:
            audit.likely_seeded_lines += 1
        if row.applied_modifiers:
            audit.lines_with_modifiers += 1
            if seeded:
                audit.seeded_lines_with_modifiers += 1
        if row.menu_item_id in proxy_items:
            lines, units = by_proxy.get(row.menu_item_id, (0, Decimal("0")))
            by_proxy[row.menu_item_id] = (lines + 1, units + row.qty)
        if row.qty <= 0:
            continue
        expected = row.price_pence * row.qty
        delta = Decimal(row.gross_pence) - expected
        if delta == 0:
            continue
        if delta < 0:
            below += 1
            continue
        per_unit = delta / row.qty
        if per_unit != per_unit.to_integral_value():
            # A surcharge that does not divide evenly into the quantity is not a
            # per-unit modifier. Reported as a below/above oddity rather than binned
            # with the clean ones, because binning it would invent a penny value.
            residue_lines.setdefault(-1, []).append(
                f"{row.lightspeed_receipt_id}/{row.lightspeed_line_id}: {delta}p over list "
                f"across {row.qty} unit(s) -- does not divide evenly, so it is not a "
                "per-unit modifier"
            )
            continue
        residue_lines.setdefault(int(per_unit), []).append(
            f"{row.lightspeed_receipt_id}/{row.lightspeed_line_id}: {row.name!r} "
            f"x{row.qty} rung at {row.gross_pence}p against a {row.price_pence}p list price"
        )

    audit.lines_priced_below_list = below
    audit.proxy_use = tuple(
        _proxy_use(proxy_items[item_id], *by_proxy.get(item_id, (0, Decimal("0"))))
        for item_id in sorted(proxy_items)
    )
    audit.residues = tuple(
        ResidueGroup(
            residue_pence=amount,
            lines=len(examples),
            explanations=tuple(price_index.get(amount, ())),
            examples=tuple(examples[:4]),
        )
        for amount, examples in sorted(residue_lines.items())
        if amount >= 0
    )
    uneven = residue_lines.get(-1, [])

    if audit.likely_seeded_lines:
        audit.caveats.append(
            f"{audit.likely_seeded_lines} of {audit.lines_considered} line(s) look SEEDED "
            f"(receipt id starts {SEEDED_RECEIPT_PREFIX!r}) and are excluded from the verdict. "
            "That is a prefix match, not a fact: `sale` has no provenance column. A "
            "`sale.source` enum -- as `channel_metric.source` already has -- would make this "
            "exact."
        )
    audit.caveats.append(
        "A residue is EVIDENCE, not a measurement. It must never deplete an ingredient: "
        "an alt milk and a syrup pump are both 40p, so +40p cannot say which was sold."
    )
    audit.caveats.append(
        "`menu_item.price_pence` is single-valued -- this schema keeps no sell-price "
        "history -- so a price raised since a sale was rung shows up here as a residue on "
        "every older line. Check the list prices before reading a large group as modifiers."
    )
    if below:
        audit.caveats.append(
            f"{below} line(s) were rung BELOW list price. Discounts and meal deals do that, "
            "and so does a price cut since the sale. Counted, not interpreted."
        )
    if uneven:
        audit.caveats.extend(uneven[:4])
    if not any(p.lightspeed_id for p in audit.proxy_use):
        audit.caveats.append(
            "No proxy item has a `lightspeed_id` yet, so a rung upcharge line could not be "
            "matched even if it arrived. Run `cafeops sync` once against the catalog first."
        )
    return audit


def _in_window(sold_at: datetime, since: date, until: date) -> bool:
    local_day = sold_at.astimezone(settings.tz).date()
    return since <= local_day <= until


@dataclass(frozen=True, slots=True)
class _ProxyItem:
    menu_item_id: int
    name: str
    lightspeed_id: str | None
    adds_ingredient_id: int
    adds_ingredient: str
    adds_qty: Decimal
    credits_ingredient: str | None
    credits_qty: Decimal | None


def _proxy_use(item: _ProxyItem, lines: int, units: Decimal) -> ProxyItemUse:
    return ProxyItemUse(
        menu_item_id=item.menu_item_id,
        name=item.name,
        lightspeed_id=item.lightspeed_id,
        adds_ingredient_id=item.adds_ingredient_id,
        adds_ingredient=item.adds_ingredient,
        adds_qty=item.adds_qty,
        credits_ingredient=item.credits_ingredient,
        credits_qty=item.credits_qty,
        lines_rung=lines,
        units_rung=units,
    )


def _proxy_items(session: Session) -> dict[int, _ProxyItem]:
    """Menu items whose live manual recipe contains a negative line.

    The negative is the whole signature: a recipe that *removes* an ingredient is a
    delta against another drink, not a drink. Recognised structurally rather than by
    the `(+upcharge)` in the name, which is presentation and will be tidied one day.
    """
    rows = session.execute(
        select(
            ManualRecipeLine.menu_item_id,
            ManualRecipeLine.qty,
            MenuItem.name,
            MenuItem.lightspeed_id,
            ManualRecipeLine.ingredient_id,
        )
        .join(MenuItem, MenuItem.id == ManualRecipeLine.menu_item_id)
        .where(ManualRecipeLine.effective_to.is_(None))
    ).all()
    grouped: dict[int, list[tuple[int, Decimal, str, str | None]]] = {}
    for menu_item_id, qty, name, lightspeed_id, ingredient_id in rows:
        grouped.setdefault(menu_item_id, []).append((ingredient_id, qty, name, lightspeed_id))

    names = dict(session.execute(select(MenuItem.id, MenuItem.name)).all())
    ingredient_names = _ingredient_names(session)

    out: dict[int, _ProxyItem] = {}
    for menu_item_id, entries in grouped.items():
        added = [(i, q) for i, q, _n, _l in entries if q > 0]
        removed = [(i, q) for i, q, _n, _l in entries if q < 0]
        if not removed or len(added) != 1:
            continue
        lightspeed_id = entries[0][3]
        credit_id, credit_qty = removed[0] if len(removed) == 1 else (None, None)
        out[menu_item_id] = _ProxyItem(
            menu_item_id=menu_item_id,
            name=names.get(menu_item_id, "?"),
            lightspeed_id=lightspeed_id,
            adds_ingredient_id=added[0][0],
            adds_ingredient=ingredient_names.get(added[0][0], f"ingredient {added[0][0]}"),
            adds_qty=added[0][1],
            credits_ingredient=(
                ingredient_names.get(credit_id, f"ingredient {credit_id}")
                if credit_id is not None
                else None
            ),
            credits_qty=credit_qty,
        )
    return out


def _ingredient_names(session: Session) -> dict[int, str]:
    from cafeops.db.models import Ingredient

    return dict(session.execute(select(Ingredient.id, Ingredient.name)).all())


def _price_index(session: Session) -> dict[int, Sequence[str]]:
    """pence -> everything the menu prices at exactly that, so a residue can be
    attributed *or shown to be unattributable*. The second is the common answer."""
    index: dict[int, list[str]] = {}
    for name, price in session.execute(
        select(Modifier.name, Modifier.price_pence).where(Modifier.is_active.is_(True))
    ).all():
        if price:
            index.setdefault(price, []).append(f"modifier {name!r}")
    for name, delta in session.execute(
        select(VariantOption.name, VariantOption.price_delta_pence)
    ).all():
        if delta:
            index.setdefault(delta, []).append(f"variant option {name!r}")
    # Add-on shaped MENU ITEMS belong here too, and their absence was the first thing
    # this probe got wrong. A 40p syrup pump is modelled in this system as a rung item,
    # not a modifier -- but a till is free to attach it as a modifier, in which case it
    # lands in the residue at exactly the same 40p as an alt milk. Leaving them out made
    # +40p look unambiguously like a milk when it is not.
    #
    # "Add-on shaped" is structural: a live manual recipe of exactly one ingredient. A
    # real drink has four to six. Keying off "(add-on)" in the name would break the day
    # the menu is tidied, and the name is not where the meaning lives (8I).
    for name, price in _single_ingredient_items(session):
        if price:
            index.setdefault(price, []).append(f"add-on item {name!r} (normally rung as a line)")
    return {amount: tuple(sorted(set(labels))) for amount, labels in index.items()}


def _single_ingredient_items(session: Session) -> list[tuple[str, int]]:
    counts: dict[int, int] = {}
    for (menu_item_id,) in session.execute(
        select(ManualRecipeLine.menu_item_id).where(ManualRecipeLine.effective_to.is_(None))
    ).all():
        counts[menu_item_id] = counts.get(menu_item_id, 0) + 1
    singles = [item_id for item_id, n in counts.items() if n == 1]
    if not singles:
        return []
    return [
        (name, price)
        for name, price in session.execute(
            select(MenuItem.name, MenuItem.price_pence).where(MenuItem.id.in_(singles))
        ).all()
    ]
