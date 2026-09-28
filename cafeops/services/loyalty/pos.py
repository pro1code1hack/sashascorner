"""Stamps from Lightspeed sales (phase 3): who a receipt belongs to, and what it earns.

docs/loyalty/CONTRACT.md "Phase 3". How a K-Series receipt names a customer: the
Financial API's sales (`GET .../sales?include=consumer`) carry a `consumer` object --
`id`, `firstName`, `lastName`, `email`, `phoneNumber1` -- when staff attached a customer
account to the check. Nothing else on a receipt identifies a person (payments carry no
card fingerprint, and this repo only ingests daily payment totals). So:

1. **Record** (`record_receipt_customers`, called by the sync for every receipt with a
   consumer): `loyalty_pos_receipt` keeps the receipt id, the customer id, the close
   time, the total, and HMACs of the normalised email/phone -- never the contacts
   themselves, so a non-member's email is not stored anywhere.
2. **Link** a member to a customer id: automatically (`auto_link`) when a receipt's email
   hash, else phone hash, matches exactly ONE live unlinked member; or explicitly
   (`link_customer`) from the scanner or the back office, by customer id or by a receipt
   number. One customer, one member (`lightspeed_customer_id` is UNIQUE).
3. **Reconcile** (`reconcile`, only when `CAFEOPS_LOYALTY_AUTO_STAMP=true`): for every
   receipt of a linked member, closed after they joined, and every live card of theirs,
   work out what the receipt is worth NOW and make the ledger agree:

   - STAMPS: one per eligible line item (programme eligibility, default = drinks), at
     most `max_stamps_per_scan` per receipt; POINTS: whole points on the eligible spend.
   - Voided lines count nothing. Refund lines net against the receipt they are on; a
     refund rung on a LATER receipt is matched to the member's most recent earlier
     receipt (within `_REFUND_LOOKBACK`) that sold the same item, and nets there.
   - **Dedupe window:** a staff PURCHASE scan on the same card within
     `loyalty_pos_dedupe_minutes` (30) either side of the receipt's close time means the
     visit was stamped at the till: the receipt earns nothing (SKIPPED_STAFF_SCAN).
   - Idempotent per (card, receipt): `loyalty_pos_award` holds the answer and the live
     PURCHASE event (note `lightspeed:<receipt>`). When the answer changes on a later
     sync (a void, a refund, a corrected quantity) the old event is reversed with an UNDO
     row and a new one written. A reversal the ledger refuses -- the reward that receipt
     completed has already been given -- freezes the row as KEPT and says so, exactly as
     the referral path does. A manager's manual undo of an auto stamp also freezes it:
     the sync must not fight a person.

Auto-stamp events are dated when the sync writes them (the card's `updated_at` must move
forward for Apple's "changed since"), not at the receipt's time; the receipt time is on
the award row.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyPosAward,
    LoyaltyPosReceipt,
    LoyaltyProgram,
    LoyaltyStampEvent,
    PosAwardStatus,
    ProgramKind,
    Sale,
    StampReason,
)
from cafeops.domain.loyalty import (
    ReceiptLine,
    contact_hash,
    item_matches,
    normalise_email,
    normalise_phone,
    receipt_units,
    staff_scan_covers,
)
from cafeops.services.loyalty.common import audit, now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.programs import item_facts, program_rule
from cafeops.services.loyalty.scan import is_undone
from cafeops.services.loyalty.stamping import apply_units, reverse_event

__all__ = [
    "PosCustomer",
    "PosReport",
    "ReceiptCustomer",
    "auto_link",
    "link_customer",
    "member_receipts",
    "pos_pass",
    "recent_customers",
    "reconcile",
    "record_receipt_customers",
    "unlink_customer",
]

#: How far back a refund on a later receipt looks for the purchase it refunds.
_REFUND_LOOKBACK = timedelta(days=30)
NOTE_PREFIX = "lightspeed:"


@dataclass(frozen=True, slots=True)
class ReceiptCustomer:
    """The customer a receipt names, as the Lightspeed mapper hands it over."""

    receipt_id: str
    customer_id: str
    closed_at: datetime
    email: str | None
    phone: str | None
    first_name: str | None
    last_name: str | None
    total_pence: int


@dataclass
class PosReport:
    enabled: bool = False
    receipts_recorded: int = 0
    links: list[str] = field(default_factory=list)
    awarded: list[str] = field(default_factory=list)
    reversed: list[str] = field(default_factory=list)
    skipped_staff: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        head = (
            f"loyalty: {self.receipts_recorded} receipt(s) with a till customer recorded, "
            f"{len(self.links)} member link(s) made"
        )
        if not self.enabled:
            return [head + "; auto-stamping OFF (CAFEOPS_LOYALTY_AUTO_STAMP)"]
        out = [
            head + f"; {len(self.awarded)} award(s), {len(self.reversed)} reversal(s), "
            f"{len(self.skipped_staff)} skipped (already scanned), {len(self.kept)} kept"
        ]
        out += [f"  LOYALTY LINK: {m}" for m in self.links]
        out += [f"  LOYALTY AWARD: {m}" for m in self.awarded]
        out += [f"  LOYALTY REVERSED: {m}" for m in self.reversed]
        out += [f"  LOYALTY SKIPPED: {m}" for m in self.skipped_staff]
        out += [f"  LOYALTY KEPT: {m}" for m in self.kept]
        return out


# --------------------------------------------------------------------------
# 1. record
# --------------------------------------------------------------------------


def _hash_email(raw: str | None) -> str | None:
    email = normalise_email(raw) if raw else None
    return contact_hash(email, settings.loyalty_key) if email else None


def _hash_phone(raw: str | None) -> str | None:
    phone = normalise_phone(raw) if raw else None
    return contact_hash(phone, settings.loyalty_key) if phone else None


def _label(first: str | None, last: str | None) -> str | None:
    first = " ".join((first or "").split())[:40]
    last = " ".join((last or "").split())
    if not first and not last:
        return None
    return f"{first} {last[:1]}.".strip() if last else first


def record_receipt_customers(
    session: Session, customers: Iterable[ReceiptCustomer], *, now: datetime | None = None
) -> int:
    """Upsert one `loyalty_pos_receipt` per receipt that named a customer. Returns how many
    rows were new or changed."""
    now = now or now_utc()
    changed = 0
    for c in customers:
        customer_id = c.customer_id.strip()[:64]
        if not customer_id:
            continue
        values = {
            "lightspeed_customer_id": customer_id,
            "closed_at": c.closed_at,
            "email_hash": _hash_email(c.email),
            "phone_hash": _hash_phone(c.phone),
            "customer_label": _label(c.first_name, c.last_name),
            "total_pence": c.total_pence,
        }
        row = session.get(LoyaltyPosReceipt, c.receipt_id)
        if row is None:
            session.add(
                LoyaltyPosReceipt(lightspeed_receipt_id=c.receipt_id, first_seen_at=now, **values)
            )
            changed += 1
            continue
        if any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            changed += 1
    session.flush()
    return changed


# --------------------------------------------------------------------------
# 2. link
# --------------------------------------------------------------------------


def auto_link(session: Session, *, now: datetime | None = None) -> list[str]:
    """Link unlinked customers whose receipt contact matches exactly one live member."""
    now = now or now_utc()
    linked_ids = set(
        session.scalars(
            select(LoyaltyMember.lightspeed_customer_id).where(
                LoyaltyMember.lightspeed_customer_id.is_not(None)
            )
        )
    )
    candidates = list(
        session.scalars(
            select(LoyaltyMember).where(
                LoyaltyMember.deleted_at.is_(None), LoyaltyMember.lightspeed_customer_id.is_(None)
            )
        )
    )
    if not candidates:
        return []
    by_email: dict[str, list[LoyaltyMember]] = defaultdict(list)
    by_phone: dict[str, list[LoyaltyMember]] = defaultdict(list)
    for m in candidates:
        if m.email:
            by_email[contact_hash(m.email, settings.loyalty_key)].append(m)
        if m.phone:
            by_phone[contact_hash(m.phone, settings.loyalty_key)].append(m)

    out: list[str] = []
    rows = session.execute(
        select(
            LoyaltyPosReceipt.lightspeed_customer_id,
            LoyaltyPosReceipt.email_hash,
            LoyaltyPosReceipt.phone_hash,
        ).order_by(LoyaltyPosReceipt.closed_at.desc())
    ).all()
    seen: set[str] = set()
    for customer_id, email_hash, phone_hash in rows:
        if customer_id in linked_ids or customer_id in seen:
            continue
        seen.add(customer_id)
        member: LoyaltyMember | None = None
        source = ""
        if email_hash and len(by_email.get(email_hash, ())) == 1:
            member, source = by_email[email_hash][0], "auto_email"
        elif phone_hash and len(by_phone.get(phone_hash, ())) == 1:
            member, source = by_phone[phone_hash][0], "auto_phone"
        if member is None or member.lightspeed_customer_id is not None:
            continue
        _set_link(session, member, customer_id, source, now)
        linked_ids.add(customer_id)
        out.append(f"member #{member.id} <- till customer {customer_id} ({source})")
    return out


def _set_link(
    session: Session, member: LoyaltyMember, customer_id: str, source: str, now: datetime
) -> None:
    member.lightspeed_customer_id = customer_id
    member.lightspeed_linked_at = now
    member.lightspeed_link_source = source
    audit(
        session,
        "pos_link",
        f"linked to Lightspeed customer {customer_id} ({source})",
        member_id=member.id,
        at=now,
    )
    session.flush()


def link_customer(
    session: Session,
    member: LoyaltyMember,
    *,
    customer_id: str | None = None,
    receipt_id: str | None = None,
    source: str,
) -> str:
    """Link by customer id, or by a receipt number whose customer is known. Returns the id."""
    if member.deleted_at is not None:
        raise LoyaltyError(409, "card_voided", "This member's card has been deleted.")
    cid = (customer_id or "").strip()[:64]
    if not cid and receipt_id:
        row = session.get(LoyaltyPosReceipt, receipt_id.strip())
        if row is None:
            raise LoyaltyError(
                404,
                "unknown_receipt",
                "No synced receipt with that number names a customer. Attach the customer "
                "on the till, wait for the next sync, or type the customer number.",
            )
        cid = row.lightspeed_customer_id
    if not cid:
        raise LoyaltyError(
            422, "customer_required", "Give a Lightspeed customer or receipt number."
        )
    other = session.scalar(
        select(LoyaltyMember).where(
            LoyaltyMember.lightspeed_customer_id == cid, LoyaltyMember.id != member.id
        )
    )
    if other is not None:
        raise LoyaltyError(
            409,
            "customer_taken",
            f"Till customer {cid} is already linked to another member. Unlink it there first.",
        )
    if member.lightspeed_customer_id == cid:
        return cid
    _set_link(session, member, cid, source, now_utc())
    return cid


def unlink_customer(session: Session, member: LoyaltyMember, *, source: str) -> None:
    """Forget the link. Stamps already given stay (they were real visits)."""
    if member.lightspeed_customer_id is None:
        return
    old = member.lightspeed_customer_id
    member.lightspeed_customer_id = None
    member.lightspeed_linked_at = None
    member.lightspeed_link_source = None
    audit(
        session, "pos_unlink", f"unlinked Lightspeed customer {old} ({source})", member_id=member.id
    )


@dataclass(frozen=True, slots=True)
class PosCustomer:
    customer_id: str
    label: str | None
    last_receipt_id: str
    last_at: datetime
    receipts: int


def recent_customers(
    session: Session, *, since: datetime, unlinked_only: bool = True, limit: int = 20
) -> list[PosCustomer]:
    """Till customers on recent receipts, newest first -- the scanner's link list."""
    linked = set(
        session.scalars(
            select(LoyaltyMember.lightspeed_customer_id).where(
                LoyaltyMember.lightspeed_customer_id.is_not(None)
            )
        )
    )
    grouped: dict[str, list[LoyaltyPosReceipt]] = defaultdict(list)
    for row in session.scalars(
        select(LoyaltyPosReceipt)
        .where(LoyaltyPosReceipt.closed_at >= since)
        .order_by(LoyaltyPosReceipt.closed_at.desc())
    ):
        if unlinked_only and row.lightspeed_customer_id in linked:
            continue
        grouped[row.lightspeed_customer_id].append(row)
    out = [
        PosCustomer(
            customer_id=cid,
            label=next((r.customer_label for r in rows if r.customer_label), None),
            last_receipt_id=rows[0].lightspeed_receipt_id,
            last_at=rows[0].closed_at,
            receipts=len(rows),
        )
        for cid, rows in grouped.items()
    ]
    out.sort(key=lambda c: c.last_at, reverse=True)
    return out[:limit]


def member_receipts(
    session: Session, customer_id: str, *, limit: int = 20
) -> list[LoyaltyPosReceipt]:
    return list(
        session.scalars(
            select(LoyaltyPosReceipt)
            .where(LoyaltyPosReceipt.lightspeed_customer_id == customer_id)
            .order_by(LoyaltyPosReceipt.closed_at.desc())
            .limit(limit)
        )
    )


# --------------------------------------------------------------------------
# 3. reconcile
# --------------------------------------------------------------------------


@dataclass
class _Line:
    menu_item_id: int
    qty: int
    gross_pence: int
    voided: bool


def _whole(qty: Decimal) -> int:
    """Sale quantities are exact decimals; a stamp is per whole item, so truncate."""
    return int(qty)


def _receipt_lines(session: Session, receipt_ids: list[str]) -> dict[str, list[_Line]]:
    out: dict[str, list[_Line]] = defaultdict(list)
    if not receipt_ids:
        return out
    for sale in session.scalars(
        select(Sale).where(Sale.lightspeed_receipt_id.in_(receipt_ids)).order_by(Sale.id)
    ):
        qty = _whole(sale.qty)
        if sale.is_refund and qty > 0:
            qty = -qty
        out[sale.lightspeed_receipt_id].append(
            _Line(sale.menu_item_id, qty, sale.gross_pence, sale.voided)
        )
    return out


def _net_refunds(
    receipts: list[LoyaltyPosReceipt], lines: dict[str, list[_Line]]
) -> dict[str, list[_Line]]:
    """Move each refund line on a refund-only receipt onto the purchase it refunds.

    A receipt with any positive line keeps its refunds (they net in place). A receipt of
    refunds only is matched line by line to the most recent earlier receipt of the same
    customer, within the lookback, that sold that item; unmatched refunds drop (there is
    no stamp to take back).
    """
    netted = {rid: list(ls) for rid, ls in lines.items()}
    ordered = sorted(receipts, key=lambda r: r.closed_at)
    for i, receipt in enumerate(ordered):
        own = netted.get(receipt.lightspeed_receipt_id, [])
        live = [ln for ln in own if not ln.voided]
        if not live or any(ln.qty > 0 for ln in live):
            continue
        keep: list[_Line] = []
        for refund in own:
            if refund.voided or refund.qty >= 0:
                keep.append(refund)
                continue
            target = None
            for earlier in reversed(ordered[:i]):
                if receipt.closed_at - earlier.closed_at > _REFUND_LOOKBACK:
                    break
                if any(
                    ln.menu_item_id == refund.menu_item_id and ln.qty > 0 and not ln.voided
                    for ln in netted.get(earlier.lightspeed_receipt_id, [])
                ):
                    target = earlier.lightspeed_receipt_id
                    break
            if target is not None:
                netted[target].append(refund)
        netted[receipt.lightspeed_receipt_id] = keep
    return netted


def _staff_scans(session: Session, card_id: str, around: datetime, minutes: int) -> list[datetime]:
    window = timedelta(minutes=minutes)
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.card_id == card_id, LoyaltyStampEvent.reason == StampReason.UNDO
    )
    return list(
        session.scalars(
            select(LoyaltyStampEvent.created_at).where(
                LoyaltyStampEvent.card_id == card_id,
                LoyaltyStampEvent.reason == StampReason.PURCHASE,
                LoyaltyStampEvent.staff_user_id.is_not(None),
                LoyaltyStampEvent.created_at >= around - window,
                LoyaltyStampEvent.created_at <= around + window,
                LoyaltyStampEvent.id.not_in(undone),
            )
        )
    )


def _units_for(
    session: Session,
    program: LoyaltyProgram,
    lines: list[_Line],
) -> int:
    facts = item_facts(session, (ln.menu_item_id for ln in lines))
    rule = program_rule(program)
    return receipt_units(
        (
            ReceiptLine(
                menu_item_id=ln.menu_item_id,
                qty=ln.qty,
                gross_pence=ln.gross_pence,
                voided=ln.voided,
                eligible=ln.menu_item_id in facts and item_matches(rule, facts[ln.menu_item_id]),
            )
            for ln in lines
        ),
        kind=program.kind.value,
        max_per_receipt=program.max_stamps_per_scan,
        points_per_pound=program.points_per_pound,
    )


def _settle(
    session: Session,
    card: LoyaltyCard,
    receipt: LoyaltyPosReceipt,
    units: int,
    skipped: bool,
    report: PosReport,
    now: datetime,
) -> None:
    rid = receipt.lightspeed_receipt_id
    program = card.program
    who = f"{rid} -> {program.name} card of member #{card.member_id}"
    note = f"{NOTE_PREFIX}{rid}"
    award = session.scalar(
        select(LoyaltyPosAward).where(
            LoyaltyPosAward.card_id == card.id, LoyaltyPosAward.lightspeed_receipt_id == rid
        )
    )
    if award is None:
        award = LoyaltyPosAward(
            card_id=card.id,
            lightspeed_receipt_id=rid,
            units=0,
            status=PosAwardStatus.NOTHING,
            created_at=now,
            updated_at=now,
        )
        session.add(award)
    if award.status is PosAwardStatus.KEPT:
        return
    live_event = None
    if award.stamp_event_id is not None:
        live_event = session.get(LoyaltyStampEvent, award.stamp_event_id)
        if live_event is not None and is_undone(session, live_event.id):
            award.status = PosAwardStatus.KEPT
            award.detail = "undone by hand at the till or in the back office; left alone"
            award.updated_at = now
            report.kept.append(f"{who}: undone by hand, left alone")
            return
    target = 0 if skipped else units
    current = award.units if live_event is not None else 0
    if target == current and (live_event is not None or target == 0):
        if skipped and award.status is not PosAwardStatus.SKIPPED_STAFF_SCAN and target == 0:
            award.status = PosAwardStatus.SKIPPED_STAFF_SCAN
            award.detail = "staff scanned this card for the same visit"
            award.updated_at = now
            report.skipped_staff.append(who)
        return
    if live_event is not None:
        try:
            reverse_event(session, live_event, note=f"{note} changed on the till (now {target})")
        except LoyaltyError as exc:
            award.status = PosAwardStatus.KEPT
            award.detail = f"reversal refused: {exc.detail}"[:400]
            award.updated_at = now
            audit(
                session,
                "pos_kept",
                f"receipt {rid} changed on the till but its stamp stays: {exc.detail}",
                card_id=card.id,
                member_id=card.member_id,
                at=now,
            )
            report.kept.append(f"{who}: {exc.detail}")
            return
        report.reversed.append(f"{who}: -{current}")
        award.stamp_event_id = None
        award.units = 0
    if target > 0 and program.active:
        event = apply_units(session, card, target, note=note, now=now)
        award.stamp_event_id = event.id
        award.units = target
        award.status = PosAwardStatus.AWARDED
        award.detail = None
        report.awarded.append(
            f"{who}: +{target} {'point' if program.kind is ProgramKind.POINTS else 'stamp'}(s)"
        )
    else:
        award.status = PosAwardStatus.SKIPPED_STAFF_SCAN if skipped else PosAwardStatus.NOTHING
        award.detail = (
            "staff scanned this card for the same visit"
            if skipped
            else (
                "programme paused"
                if target > 0
                else "nothing eligible left on this receipt (none, voided or refunded)"
            )
        )
        if skipped:
            report.skipped_staff.append(who)
    award.updated_at = now
    session.flush()


def reconcile(
    session: Session,
    *,
    since: datetime | None = None,
    member_ids: Iterable[int] | None = None,
    now: datetime | None = None,
    report: PosReport | None = None,
) -> PosReport:
    """Make every linked member's cards agree with their receipts. See the module doc."""
    now = now or now_utc()
    report = report or PosReport()
    report.enabled = settings.loyalty_auto_stamp
    if not report.enabled:
        return report
    stmt = select(LoyaltyMember).where(
        LoyaltyMember.deleted_at.is_(None), LoyaltyMember.lightspeed_customer_id.is_not(None)
    )
    if member_ids is not None:
        stmt = stmt.where(LoyaltyMember.id.in_(list(member_ids)))
    minutes = settings.loyalty_pos_dedupe_minutes
    for member in session.scalars(stmt).all():
        customer_id = member.lightspeed_customer_id
        assert customer_id is not None
        floor = member.created_at
        if since is not None:
            floor = max(floor, since - _REFUND_LOOKBACK)
        receipts = list(
            session.scalars(
                select(LoyaltyPosReceipt).where(
                    LoyaltyPosReceipt.lightspeed_customer_id == customer_id,
                    LoyaltyPosReceipt.closed_at >= floor,
                )
            )
        )
        if not receipts:
            continue
        lines = _net_refunds(
            receipts, _receipt_lines(session, [r.lightspeed_receipt_id for r in receipts])
        )
        cards = list(
            session.scalars(
                select(LoyaltyCard).where(
                    LoyaltyCard.member_id == member.id, LoyaltyCard.voided_at.is_(None)
                )
            )
        )
        for receipt in sorted(receipts, key=lambda r: r.closed_at):
            if receipt.closed_at < member.created_at:
                continue
            if since is not None and receipt.closed_at < since:
                continue
            receipt_lines = lines.get(receipt.lightspeed_receipt_id, [])
            for card in cards:
                units = _units_for(session, card.program, receipt_lines)
                skipped = units > 0 and staff_scan_covers(
                    receipt.closed_at,
                    _staff_scans(session, card.id, receipt.closed_at, minutes),
                    minutes,
                )
                _settle(session, card, receipt, units, skipped, report, now)
    return report


def pos_pass(
    session: Session,
    customers: Iterable[ReceiptCustomer],
    *,
    since: datetime | None,
    now: datetime | None = None,
) -> PosReport:
    """What the sync calls after ingesting sales: record, link, reconcile."""
    now = now or now_utc()
    report = PosReport(enabled=settings.loyalty_auto_stamp)
    report.receipts_recorded = record_receipt_customers(session, customers, now=now)
    report.links = auto_link(session, now=now)
    return reconcile(session, since=since, now=now, report=report)
