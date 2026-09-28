"""Sasha's Corner Rewards: every write the loyalty feature makes goes through here.

docs/loyalty/CONTRACT.md is binding. The API (`api/areas/loyalty*`, `staff*`, `members*`),
the scheduler (`jobs/loyalty_*`), the bot's `/member` and the CLI are all clients of these
modules; none of them touches a loyalty table directly (CLAUDE.md §8).

The arithmetic lives in `cafeops/domain/loyalty.py` and is pure. What lives here is the
part with a session in it: which rows, in which order, in one transaction -- including the
wallet outbox row, so a stamp and "tell the pass" commit or roll back together.

Modules: `common` (lookups, audit, the outbox), `card_view`, `join`, `staff_auth`,
`stamping` (stamp, migrate, adjust, undo, scan), `redeem`, `recovery`, `messaging`
(SMTP/Twilio, called after commit), `admin`, `campaigns`, `stats`, `birthdays`,
`retention` (erasure), `alerts`, `wallets` (the guarded seam to the wallet module).

Every service raises `LoyaltyError(status, code, detail)` for a refusal a person should
read; the API renders it as `{"error": code, "detail": detail}` (CONTRACT §4).
"""

from cafeops.services.loyalty.errors import LoyaltyError

__all__ = ["LoyaltyError"]
