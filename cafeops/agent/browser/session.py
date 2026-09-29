"""Persistent browser profiles and the transport of a sign-in.

docs/agents/BROWSER-ORDERING.md §6. The sign-in lives in a Chromium profile directory,
one per supplier, under `settings.browser_data_dir` (default: next to the SQLite
file, `<db dir>/browser`). The database never holds a password; what it may hold is
a Playwright storage state (cookies and localStorage), Fernet-encrypted with
`CAFEOPS_BROWSER_SESSION_KEY`, so a sign-in made on the owner's laptop can be copied
to the server and seed the server's profile on first use.

Playwright is imported lazily inside `open_supplier_browser`: the API, the scheduler
and the CLI import this module without a browser installed.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import make_url

from cafeops.agent.browser.types import BrowserAction, BrowserExecutor
from cafeops.config import REPO_ROOT, settings

if TYPE_CHECKING:
    from cafeops.db.models import SupplierSession
    from cafeops.integrations.suppliers.portals.base import SupplierPortal


class SessionKeyMissing(RuntimeError):
    """`CAFEOPS_BROWSER_SESSION_KEY` is unset, so a storage state cannot be moved."""

    def __init__(self) -> None:
        super().__init__(
            "CAFEOPS_BROWSER_SESSION_KEY is not set. Importing or exporting a supplier "
            "sign-in stores a Playwright storage state (cookies) in the database, and that "
            "is only ever done encrypted. Generate one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and put it in .env on BOTH machines. The browser profile directory works "
            "without it; only the copy between machines needs it."
        )


# ==========================================================================
# Profile directories
# ==========================================================================


def browser_data_dir() -> Path:
    """`settings.browser_data_dir`, else `<sqlite directory>/browser`, else
    `<repo>/data/browser` when the database is not a file."""
    if settings.browser_data_dir is not None:
        return Path(settings.browser_data_dir)
    try:
        url = make_url(settings.database_url)
    except Exception:
        return REPO_ROOT / "data" / "browser"
    database = url.database
    if url.get_backend_name() == "sqlite" and database and database != ":memory:":
        return Path(database).resolve().parent / "browser"
    return REPO_ROOT / "data" / "browser"


def profile_dir_name(supplier_id: int, slug: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-") or "portal"
    return f"{supplier_id}-{safe}"


def profile_dir_for(supplier_id: int, slug: str) -> Path:
    """The supplier's profile directory, created 0o700 (cookies live in it)."""
    base = browser_data_dir()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = base / profile_dir_name(supplier_id, slug)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - a read-only or foreign filesystem
        pass
    return path


# ==========================================================================
# Storage state at rest
# ==========================================================================


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    key = settings.browser_session_key
    if not key:
        raise SessionKeyMissing()
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt_state(state: dict[str, Any]) -> bytes:
    """A Playwright storage state, Fernet-encrypted for `supplier_session.storage_state_enc`."""
    payload = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token: bytes = _fernet().encrypt(payload)
    return token


def decrypt_state(blob: bytes) -> dict[str, Any]:
    from cryptography.fernet import InvalidToken

    try:
        raw: bytes = _fernet().decrypt(blob)
    except InvalidToken as exc:
        raise ValueError(
            "the stored storage state does not decrypt with CAFEOPS_BROWSER_SESSION_KEY; "
            "the key differs from the one it was encrypted with, or the blob is damaged"
        ) from exc
    state = json.loads(raw.decode("utf-8"))
    if not isinstance(state, dict):
        raise ValueError("decrypted storage state is not an object")
    return state


# ==========================================================================
# Opening a browser for a supplier
# ==========================================================================


def open_supplier_browser(
    session_row: SupplierSession,
    portal: SupplierPortal,
    *,
    headless: bool | None = None,
    downloads_dir: Path | None = None,
) -> BrowserExecutor:
    """Launch the supplier's persistent profile with the portal's policy.

    Sets `session_row.profile_dir` (the caller commits). When the profile has not
    been used yet on this machine (no `Default` directory) and an encrypted storage
    state is stored, it is decrypted and handed to Playwright so the first launch is
    already signed in. After that the profile's own cookies are the truth.
    """
    from cafeops.agent.browser.executor import PlaywrightExecutor

    profile = profile_dir_for(session_row.supplier_id, portal.slug)
    session_row.profile_dir = profile.name
    storage_state: dict[str, Any] | None = None
    if not (profile / "Default").exists() and session_row.storage_state_enc:
        storage_state = decrypt_state(bytes(session_row.storage_state_enc))
    executor: BrowserExecutor = PlaywrightExecutor.launch(
        profile_dir=profile,
        policy=portal.policy,
        headless=settings.browser_headless if headless is None else headless,
        screenshot_max_px=settings.browser_screenshot_max_px,
        downloads_dir=downloads_dir,
        storage_state=storage_state,
    )
    return executor


def check_signed_in(executor: BrowserExecutor, portal: SupplierPortal) -> tuple[bool, str | None]:
    """Navigate to the portal's start page and ask the adapter. Never raises."""
    try:
        result = executor.execute(BrowserAction("navigate", {"url": portal.policy.start_url}))
        if result.is_error:
            return False, None
        page = getattr(executor, "page", None)
        if page is None:
            return False, None
        if not portal.is_signed_in(page):
            return False, None
        try:
            label = portal.account_label(page)
        except Exception:
            label = None
        return True, label
    except Exception:
        return False, None


__all__ = [
    "SessionKeyMissing",
    "browser_data_dir",
    "check_signed_in",
    "decrypt_state",
    "encrypt_state",
    "open_supplier_browser",
    "profile_dir_for",
    "profile_dir_name",
]
