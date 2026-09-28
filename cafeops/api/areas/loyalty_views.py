"""Sync assembly for the public loyalty API. Same rules as `stock_views.py`.

Nothing here is async and nothing returns an ORM object: each function runs inside
`asyncio.to_thread` and hands back a finished Pydantic model. Rules live in
`services/loyalty`; this file only converts.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.areas.loyalty_schemas import (
    CardState,
    ExtraCardOut,
    JoinIn,
    JoinResult,
    PreferencesIn,
    ProgramOut,
    ProgramsOut,
    PublicProgramOut,
    RecoverOut,
    RewardOptionOut,
    RewardOut,
    SiblingCardOut,
    VerifyIn,
    WalletLinks,
    WalletsAvailable,
)
from cafeops.db.models import LoyaltyCard, LoyaltyProgram
from cafeops.domain.loyalty import mask_contact
from cafeops.services.loyalty.card_view import CardView, card_view_of
from cafeops.services.loyalty.common import (
    DEFAULT_PROGRAM_SLUG,
    authenticate_card,
    default_program,
    now_utc,
)
from cafeops.services.loyalty.consent import set_marketing_opt_in
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.join import (
    DetailsChange,
    JoinRequest,
    add_program_card,
    birthday_counts_from,
    join,
    update_details,
)
from cafeops.services.loyalty.messaging import Outgoing
from cafeops.services.loyalty.programs import programs, reward_options
from cafeops.services.loyalty.recovery import request_recovery, verify_recovery
from cafeops.services.loyalty.retention import erase_member
from cafeops.services.loyalty.wallets import apple_configured, google_configured


def wallet_links(card_id: str, token: str) -> WalletLinks:
    return WalletLinks(
        apple_pass_url=(
            f"/api/loyalty/card/{card_id}/apple.pkpass?t={token}" if apple_configured() else None
        ),
        google_save_url=(
            f"/api/loyalty/card/{card_id}/google?t={token}" if google_configured() else None
        ),
    )


def join_result(
    card_id: str, token: str, extra: tuple[tuple[str, str, str], ...] = ()
) -> JoinResult:
    links = wallet_links(card_id, token)
    return JoinResult(
        card_id=card_id,
        token=token,
        web_card_url=f"/c/{card_id}#t={token}",
        apple_pass_url=links.apple_pass_url,
        google_save_url=links.google_save_url,
        extra_cards=[
            ExtraCardOut(
                program_slug=slug, card_id=cid, token=tok, web_card_url=f"/c/{cid}#t={tok}"
            )
            for slug, cid, tok in extra
        ],
    )


def catalogue(session: Session, p: LoyaltyProgram) -> list[RewardOptionOut]:
    return [
        RewardOptionOut(name=o.name, description=o.description, max_price_pence=o.max_price_pence)
        for o in reward_options(session, p.id)
    ]


def public_program(p: LoyaltyProgram, session: Session | None = None) -> PublicProgramOut:
    return PublicProgramOut(
        slug=p.slug,
        name=p.name,
        kind=p.kind.value,
        description=p.description,
        stamps_required=p.stamps_required,
        points_per_pound=p.points_per_pound,
        reward_text=p.reward_text,
        reward_ready_label=p.reward_ready_label,
        is_default=p.slug == DEFAULT_PROGRAM_SLUG,
        catalogue=catalogue(session, p) if session is not None else [],
    )


def programs_view(session: Session) -> ProgramsOut:
    return ProgramsOut(
        programs=[public_program(p, session) for p in programs(session, active_only=True)]
    )


def join_program_view(session: Session, card_id: str, token: str | None, slug: str) -> JoinResult:
    card = _live(session, card_id, token)
    new = add_program_card(session, card.member, slug, source="web_card")
    return join_result(new.id, new.auth_token)


def program_view(session: Session) -> ProgramOut:
    program = default_program(session)
    return ProgramOut(
        name=program.name,
        stamps_required=program.stamps_required,
        reward_text=program.reward_text,
        birthday_reward=program.birthday_reward,
        referral_stamps=program.referral_stamps,
        max_stamps_per_scan=program.max_stamps_per_scan,
        wallets=WalletsAvailable(apple=apple_configured(), google=google_configured()),
        slug=program.slug,
        kind=program.kind.value,
        description=program.description,
        reward_ready_label=program.reward_ready_label,
        reward_max_price_pence=program.reward_max_price_pence,
        points_per_pound=program.points_per_pound,
        catalogue=catalogue(session, program),
    )


def join_view(session: Session, body: JoinIn) -> JoinResult:
    joined = join(
        session,
        JoinRequest(
            first_name=body.first_name,
            email=body.email,
            phone=body.phone,
            birthday_day=body.birthday_day,
            birthday_month=body.birthday_month,
            terms=body.terms,
            marketing_opt_in=body.marketing_opt_in,
            src=body.src,
            ref=body.ref,
            also_join=tuple(body.also_join),
        ),
    )
    return join_result(joined.card_id, joined.token, joined.extra_cards)


def _live(session: Session, card_id: str, token: str | None) -> LoyaltyCard:
    card = authenticate_card(session, card_id, token)
    if card.voided_at is not None:
        raise LoyaltyError(410, "card_deleted", "This card has been deleted.")
    return card


def _state(session: Session, card: LoyaltyCard) -> CardState:
    view = card_view_of(session, card)
    member = card.member
    main = default_program(session)
    return CardState(
        card_id=view.card_id,
        first_name=view.first_name,
        member_since=view.member_since,
        program_name=view.program_name,
        stamps_required=view.stamps_required,
        stamps_current=view.stamps_current,
        reward_text=view.reward_text,
        rewards=[
            RewardOut(id=r.id, kind=r.kind, label=r.label, expires_at=r.expires_at)
            for r in view.rewards
        ],
        qr_payload=view.qr_payload,
        marketing_opt_in=view.marketing_opt_in,
        updated_at=view.updated_at,
        wallets=wallet_links(card.id, card.auth_token),
        program_slug=view.program_slug,
        program_kind=view.program_kind,
        points_per_pound=view.points_per_pound,
        reward_ready_label=view.reward_ready_label,
        program_description=view.program_description,
        other_cards=_siblings(session, card),
        joinable=_joinable(session, card),
        # Referrals pay into the main card (stamping._credit_referrer), whichever of
        # the member's cards the link was shared from.
        referral_stamps=main.referral_stamps if main.active else 0,
        birthday_reward=main.birthday_reward,
        birthday_day=member.birthday_day,
        birthday_month=member.birthday_month,
        birthday_counts_from=birthday_counts_from(member),
        contact_masked=mask_contact(member.email, member.phone),
    )


def _siblings(session: Session, card: LoyaltyCard) -> list[SiblingCardOut]:
    out: list[SiblingCardOut] = []
    for other in session.scalars(
        select(LoyaltyCard)
        .join(LoyaltyProgram, LoyaltyProgram.id == LoyaltyCard.program_id)
        .where(
            LoyaltyCard.member_id == card.member_id,
            LoyaltyCard.id != card.id,
            LoyaltyCard.voided_at.is_(None),
        )
        .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.id)
    ):
        out.append(
            SiblingCardOut(
                card_id=other.id,
                token=other.auth_token,
                program_slug=other.program.slug,
                program_name=other.program.name,
                program_kind=other.program.kind.value,
                stamps_current=other.stamps_current,
                stamps_required=other.program.stamps_required,
                reward_ready=other.reward_available,
                web_card_url=f"/c/{other.id}#t={other.auth_token}",
            )
        )
    return out


def _joinable(session: Session, card: LoyaltyCard) -> list[PublicProgramOut]:
    held = select(LoyaltyCard.program_id).where(LoyaltyCard.member_id == card.member_id)
    return [
        public_program(p)
        for p in session.scalars(
            select(LoyaltyProgram)
            .where(LoyaltyProgram.active.is_(True), LoyaltyProgram.id.not_in(held))
            .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.id)
        )
    ]


def card_state(session: Session, card_id: str, token: str | None) -> CardState:
    card = _live(session, card_id, token)
    if card.web_seen_at is None:
        # Once per card, ever: enough to say "keeps it in the browser" on the Members
        # screen without a write per page view.
        card.web_seen_at = now_utc()
    return _state(session, card)


def card_for_wallet(session: Session, card_id: str, token: str | None) -> CardView:
    return card_view_of(session, _live(session, card_id, token))


def preferences_view(
    session: Session, card_id: str, token: str | None, body: PreferencesIn
) -> CardState:
    card = _live(session, card_id, token)
    sent = body.model_fields_set
    if not sent:
        raise LoyaltyError(422, "nothing_to_change", "There was nothing to save.")
    if "marketing_opt_in" in sent:
        if body.marketing_opt_in is None:
            raise LoyaltyError(422, "invalid_request", "Say yes or no to news and offers.")
        set_marketing_opt_in(session, card, opt_in=body.marketing_opt_in, source="web_card")
    set_birthday = "birthday_day" in sent or "birthday_month" in sent
    if "first_name" in sent or set_birthday:
        if "first_name" in sent and body.first_name is None:
            raise LoyaltyError(422, "first_name_required", "Tell us your first name.")
        update_details(
            session,
            card,
            DetailsChange(
                first_name=body.first_name,
                set_birthday=set_birthday,
                birthday=(body.birthday_day, body.birthday_month),
            ),
        )
    return _state(session, card)


def delete_card_view(session: Session, card_id: str, token: str | None) -> None:
    card = authenticate_card(session, card_id, token)
    erase_member(session, card.member_id, why="deleted by the customer from the web card")


def recover_view(session: Session, contact: str) -> tuple[RecoverOut, Outgoing | None]:
    result = request_recovery(session, contact)
    return RecoverOut(delivery=result.delivery), result.outgoing


def verify_view(session: Session, body: VerifyIn) -> JoinResult | None:
    joined = verify_recovery(session, body.contact, body.code)
    if joined is None:
        return None
    return join_result(joined.card_id, joined.token)
