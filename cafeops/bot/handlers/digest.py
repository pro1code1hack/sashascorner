"""The morning digest. Read-only, on purpose.

`views.build_digest` runs the expiry sweep with `dry_run=True`, so reading the message
costs nothing and re-reading it costs nothing twice. The sweep that actually books a
write-off is a scheduled job with its own idempotency key (`stock_batch.expired_at`);
if the digest wrote, then reading the morning message would be a financial event and the
waste figure would grow every time somebody scrolled up.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from cafeops.bot import formatters as f
from cafeops.bot.deps import RunSync
from cafeops.bot.views import build_digest

router = Router(name="digest")


@router.message(Command("digest"))
async def digest(message: Message, run_sync: RunSync) -> None:
    view = await run_sync(build_digest)
    await message.answer(f.digest(view))
