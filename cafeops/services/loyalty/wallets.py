"""The seam to the wallet module (agent B, `cafeops/integrations/wallet/`).

Loyalty must work with no wallet at all: the web card is a complete card, and a server
with no Apple certificate is the normal state until the café has an Apple account. So
"is Apple/Google configured?" is asked here, and a missing certificate or setting reads
as "not configured" -- the join page then offers the web card only.

What is NOT degraded any more is a broken module. The wallet package and its models
ship with the application (`db/models/__init__.py` imports the wallet models, and
`integrations/wallet/config.py` has no optional dependency), so they are imported
statically. The one genuinely optional part is a pass builder whose third-party library
is absent -- `wallet_module` answers None for an `ImportError` and logs it once. Any
other exception while importing a wallet module is a bug and propagates: five silent
`except Exception` here used to turn one into "wallet not configured".
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from cafeops.db.models.wallet import WalletAppleRegistration, WalletGoogleObject
from cafeops.integrations.wallet.config import wallet_settings

__all__ = [
    "apple_configured",
    "google_configured",
    "wallet_kind_by_card",
    "wallet_module",
]

log = logging.getLogger("cafeops.loyalty")

#: Wallet modules whose import failed for want of a dependency, logged once each.
_unavailable: set[str] = set()
#: Databases (by URL) where the wallet tables were found. Only a positive answer is
#: cached: tables do not vanish under a running process, but a migration run while it
#: is up can create them, and a cached "absent" would hide them until a restart.
_tables_present: set[str] = set()


def wallet_module(name: str) -> Any | None:
    """`cafeops.integrations.wallet.<name>`, or None if an optional dependency of it is
    missing (logged once). Imported at call time so the API starts whatever state the
    wallet package is in."""
    try:
        return importlib.import_module(f"cafeops.integrations.wallet.{name}")
    except ImportError as exc:
        if name not in _unavailable:
            _unavailable.add(name)
            log.warning("wallet module %s unavailable (missing dependency?): %s", name, exc)
        return None


def apple_configured() -> bool:
    return wallet_settings.apple_configured and wallet_module("apple") is not None


def google_configured() -> bool:
    return wallet_settings.google_configured and wallet_module("google") is not None


def _wallet_tables_present(session: Session) -> bool:
    """Both wallet tables exist. Inspected until they do, then remembered, so the
    member list stops paying for a schema read on every render."""
    bind = session.get_bind()
    key = str(bind.engine.url)
    if key in _tables_present:
        return True
    names = set(inspect(bind).get_table_names())
    wanted = {WalletGoogleObject.__tablename__, WalletAppleRegistration.__tablename__}
    if not wanted <= names:
        return False
    _tables_present.add(key)
    return True


def wallet_kind_by_card(session: Session, card_ids: list[str]) -> dict[str, str]:
    """ "apple" if a device registered the pass, else "google" if an object exists.

    Only when the wallet tables exist -- the member list must render on a database the
    wallet migration has not reached. "web" (first web-card view) is the caller's
    fallback; this answers only for the two wallets.
    """
    if not card_ids or not _wallet_tables_present(session):
        return {}
    out: dict[str, str] = {}
    for card_id in session.scalars(
        select(WalletGoogleObject.card_id).where(WalletGoogleObject.card_id.in_(card_ids))
    ):
        out[str(card_id)] = "google"
    for card_id in session.scalars(
        select(WalletAppleRegistration.card_id).where(WalletAppleRegistration.card_id.in_(card_ids))
    ):
        out[str(card_id)] = "apple"
    return out
