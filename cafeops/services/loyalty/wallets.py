"""The guarded seam to the wallet module (agent B, `cafeops/integrations/wallet/`).

Loyalty must work with no wallet at all: the web card is a complete card, and a server
with no Apple certificate is the normal state until the café has an Apple account. So
every reach into the wallet package goes through here and degrades to "not configured"
if the package, a module in it, or a setting is missing -- the join page then offers the
web card only, and nothing 500s because a certificate is absent.

Imports are by name at call time (`importlib`), not at module import, so this file never
fails to import and the API starts whatever state the wallet package is in.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

__all__ = [
    "apple_configured",
    "google_configured",
    "wallet_kind_by_card",
    "wallet_module",
]

log = logging.getLogger("cafeops.loyalty")


def wallet_module(name: str) -> Any | None:
    """`cafeops.integrations.wallet.<name>`, or None if it is missing or broken."""
    try:
        return importlib.import_module(f"cafeops.integrations.wallet.{name}")
    except Exception as exc:  # ImportError, or a module that raises while importing
        log.debug("wallet module %s unavailable: %s", name, exc)
        return None


def _settings() -> Any | None:
    config = wallet_module("config")
    if config is None:
        return None
    settings = getattr(config, "wallet_settings", None)
    if callable(settings) and not hasattr(settings, "apple_configured"):
        try:
            settings = settings()
        except Exception:
            return None
    return settings


def apple_configured() -> bool:
    s = _settings()
    try:
        return bool(s is not None and s.apple_configured and wallet_module("apple") is not None)
    except Exception:
        return False


def google_configured() -> bool:
    s = _settings()
    try:
        return bool(s is not None and s.google_configured and wallet_module("google") is not None)
    except Exception:
        return False


def wallet_kind_by_card(session: Session, card_ids: list[str]) -> dict[str, str]:
    """ "apple" if a device registered the pass, else "google" if an object exists.

    Reads B's tables through B's models, and only when both the model module and the
    table exist -- the member list must render on a database B's migration has not
    reached. "web" (first web-card view) is the caller's fallback; this answers only for
    the two wallets.
    """
    if not card_ids:
        return {}
    try:
        models = importlib.import_module("cafeops.db.models.wallet")
    except Exception:
        return {}
    bind = session.get_bind()
    try:
        names = set(inspect(bind).get_table_names())
    except Exception:
        return {}
    out: dict[str, str] = {}
    google = getattr(models, "WalletGoogleObject", None)
    if google is not None and "wallet_google_object" in names:
        for card_id in session.scalars(select(google.card_id).where(google.card_id.in_(card_ids))):
            out[str(card_id)] = "google"
    apple = getattr(models, "WalletAppleRegistration", None)
    if apple is not None and "wallet_apple_registration" in names:
        for card_id in session.scalars(select(apple.card_id).where(apple.card_id.in_(card_ids))):
            out[str(card_id)] = "apple"
    return out
