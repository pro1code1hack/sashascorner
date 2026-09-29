"""`/member <phone|email>`: a loyalty member at a glance (CONTRACT §7). Read-only.

Owner chat only, like every handler here: the gate is on the router (`common.owner_only`),
which is why this router is listed in `handlers.ALL_ROUTERS` rather than attached alone.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from cafeops.bot import formatters as f
from cafeops.bot.deps import RunSync
from cafeops.services.loyalty.admin import member_brief

router = Router(name="member")


@router.message(Command("member"))
async def member(message: Message, command: CommandObject, run_sync: RunSync) -> None:
    contact = (command.args or "").strip()
    if not contact:
        await message.answer(f.member_usage())
        return
    brief = await run_sync(member_brief, contact)
    await message.answer(f.member_brief(brief) if brief else f.member_not_found())
