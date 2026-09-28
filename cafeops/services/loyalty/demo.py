"""`cafeops loyalty seed-demo`: a programme and three obviously fake members, for dev.

Contacts are reserved-for-fiction values (`example.com`, Ofcom's 07700 900xxx drama
range) so a demo member can never be a real customer or receive a real message.
Idempotent: a member whose contact already exists is left alone.

`programmes=True` (phase 3, `--programmes`) adds, for trying phase 3 on a dev database:
a "Matcha club" stamp card (6 matchas, a free one), a "Cake points" points card (10 points
a pound, a reward at 100), a two-entry reward catalogue on the main card (any drink, or a
slice of cake up to £4.50), and puts every demo member in the matcha club. Demo members
are dated 60 days back so the shipped Lightspeed fixture receipts (16-20 September) fall
after they joined and can be auto-stamped. Never in a migration: the café's real
programmes are the owner's to create.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import LoyaltyCard, LoyaltyMember, LoyaltyProgram, ProgramKind
from cafeops.services.loyalty.common import DEFAULT_PROGRAM_SLUG, now_utc
from cafeops.services.loyalty.join import (
    JoinRequest,
    add_program_card,
    find_member_by_contact,
    join,
)
from cafeops.services.loyalty.programs import (
    OptionChange,
    ProgramChange,
    apply_change,
    create_program,
    reward_options,
)

__all__ = ["DemoSeeded", "seed_demo"]

_DEMO = (
    JoinRequest("Anna", "anna.demo@example.com", None, None, None, True, True, "table", None),
    JoinRequest("Ben", None, "07700 900001", None, None, True, False, "till", None),
    JoinRequest("Cara", "cara.demo@example.com", "07700 900002", 14, 2, True, True, "cup", None),
)


@dataclass(frozen=True, slots=True)
class DemoSeeded:
    program_created: bool
    members_created: tuple[str, ...]
    cards: tuple[tuple[str, str, str], ...]  # (first_name, card_id, token)
    programmes_created: tuple[str, ...] = ()


_BACKDATE = timedelta(days=60)


def _seed_programmes(session: Session, default: LoyaltyProgram) -> list[str]:
    made: list[str] = []
    if (
        session.scalar(select(LoyaltyProgram.id).where(LoyaltyProgram.slug == "matcha-club"))
        is None
    ):
        create_program(
            session,
            slug="matcha-club",
            change=ProgramChange(
                name="Matcha club",
                kind=ProgramKind.STAMPS,
                stamps_required=6,
                reward_text="A matcha of your choice, on us",
                reward_ready_label="Free matcha ready",
                description="Every 6th matcha is free. Counts alongside your main card.",
                eligibility={"scope": "drinks", "keywords": ["matcha"]},
                active=True,
                sort_order=1,
                reward_options=[
                    OptionChange(
                        name="Any matcha drink",
                        eligibility={"scope": "drinks", "keywords": ["matcha"]},
                    )
                ],
            ),
        )
        made.append("matcha-club")
    if (
        session.scalar(select(LoyaltyProgram.id).where(LoyaltyProgram.slug == "cake-points"))
        is None
    ):
        create_program(
            session,
            slug="cake-points",
            change=ProgramChange(
                name="Cake points",
                kind=ProgramKind.POINTS,
                points_per_pound=10,
                stamps_required=100,
                reward_text="A slice of cake, on us",
                reward_ready_label="Cake slice ready",
                description="10 points for every pound. 100 points is a slice of cake.",
                eligibility={"scope": "all"},
                active=True,
                sort_order=2,
                reward_options=[
                    OptionChange(
                        name="Slice of cake",
                        eligibility={
                            "scope": "all",
                            "keywords": ["cake", "slice", "brownie", "cheesecake"],
                        },
                        max_price_pence=450,
                    )
                ],
            ),
        )
        made.append("cake-points")
    if not reward_options(session, default.id, active_only=False):
        apply_change(
            session,
            default,
            ProgramChange(
                reward_options=[
                    OptionChange(name="Any drink", description="Hot or cold, any size"),
                    OptionChange(
                        name="Slice of cake",
                        description="Instead of the drink",
                        eligibility={
                            "scope": "all",
                            "keywords": ["cake", "slice", "brownie", "cheesecake"],
                        },
                        max_price_pence=450,
                    ),
                ]
            ),
        )
        made.append("stamp catalogue")
    return made


def seed_demo(session: Session, *, programmes: bool = False) -> DemoSeeded:
    program = session.scalar(
        select(LoyaltyProgram).where(LoyaltyProgram.slug == DEFAULT_PROGRAM_SLUG)
    )
    created_program = program is None
    if program is None:
        session.add(
            LoyaltyProgram(
                slug=DEFAULT_PROGRAM_SLUG,
                name="Sasha's Corner Rewards",
                stamps_required=8,
                max_stamps_per_scan=3,
                reward_text="Any drink, on us",
                reward_max_price_pence=None,
                birthday_reward=True,
                referral_stamps=1,
                active=True,
                created_at=now_utc(),
            )
        )
        session.flush()
    made: list[str] = []
    if programmes:
        default = session.scalar(
            select(LoyaltyProgram).where(LoyaltyProgram.slug == DEFAULT_PROGRAM_SLUG)
        )
        assert default is not None
        made = _seed_programmes(session, default)
    names: list[str] = []
    cards: list[tuple[str, str, str]] = []
    for req in _DEMO:
        contact = req.email or req.phone or ""
        member = find_member_by_contact(session, contact)
        if member is None:
            joined = join(session, req)
            names.append(req.first_name)
            cards.append((req.first_name, joined.card_id, joined.token))
            member = session.get(LoyaltyMember, joined.member_id)
            assert member is not None
            if programmes:
                past = now_utc() - _BACKDATE
                member.created_at = member.last_activity_at = past
        if programmes and member.deleted_at is None:
            held = {
                c.program.slug
                for c in session.scalars(
                    select(LoyaltyCard).where(LoyaltyCard.member_id == member.id)
                )
            }
            if "matcha-club" not in held:
                card = add_program_card(session, member, "matcha-club", source="seed-demo")
                cards.append((f"{member.first_name} (Matcha club)", card.id, card.auth_token))
    session.flush()
    return DemoSeeded(
        program_created=created_program,
        members_created=tuple(names),
        cards=tuple(cards),
        programmes_created=tuple(made),
    )
