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
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import make_url

from cafeops.agent.browser.types import BrowserAction, BrowserExecutor
from cafeops.config import REPO_ROOT, settings
from cafeops.integrations.suppliers.portals.base import PortalNeedsHuman

if TYPE_CHECKING:
    from cafeops.db.models import SupplierSession
    from cafeops.integrations.suppliers.portals.base import SupplierPortal


log = logging.getLogger("cafeops.browser.session")


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


def has_display() -> bool:
    """Is there a screen to put a headed window on? `DISPLAY` is what X11 and
    `xvfb-run` set; `WAYLAND_DISPLAY` is the Wayland equivalent on a desktop box."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


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
    if headless is None:
        headless = settings.browser_headless
    if headless and getattr(portal, "prefers_headed", False):
        # Tier 2 (docs/agents/BROWSER-ORDERING.md §10): the site refuses headless
        # browsers (Tesco/Akamai). A visible window needs a display -- Xvfb in the
        # worker container, the real screen on a laptop. Without one, say so before
        # opening anything rather than launching a browser that will be served
        # "Access Denied".
        if has_display():
            headless = False
        else:
            raise PortalNeedsHuman(
                f"{portal.label} refuses headless browsers; run the worker with a display "
                "(xvfb: CAFEOPS_BROWSER_HEADLESS=false with deploy/browser-worker-entrypoint.sh) "
                "or stage this order by hand"
            )

    from cafeops.agent.browser.executor import PlaywrightExecutor

    profile = profile_dir_for(session_row.supplier_id, portal.slug)
    session_row.profile_dir = profile.name
    storage_state: dict[str, Any] | None = None
    if not (profile / "Default").exists() and session_row.storage_state_enc:
        storage_state = decrypt_state(bytes(session_row.storage_state_enc))
    executor: BrowserExecutor = PlaywrightExecutor.launch(
        profile_dir=profile,
        policy=portal.policy,
        headless=headless,
        screenshot_max_px=settings.browser_screenshot_max_px,
        downloads_dir=downloads_dir,
        storage_state=storage_state,
    )
    return executor


class SignInCheckFailed(RuntimeError):
    """The sign-in could not be LOOKED AT: the start page did not load (DNS, network,
    a refused navigation), the executor has no page, or the adapter itself failed
    (`PortalStepFailed("generic portal not configured")`, a Playwright error). Not
    "signed out" -- a session must not be marked EXPIRED, and a person sent to sign in
    again, because the network was down or the adapter is misconfigured."""


def check_signed_in(executor: BrowserExecutor, portal: SupplierPortal) -> tuple[bool, str | None]:
    """Navigate to the portal's start page and ask the adapter.

    Three outcomes, kept apart on purpose:

    * `(True, label)` / `(False, None)` -- the adapter looked and answered. `False` is
      the only thing that means "sign in again".
    * `PortalNeedsHuman` re-raised -- a CAPTCHA, a code, a bot wall: the job stops
      NEEDS_HUMAN with the portal's own reason.
    * `SignInCheckFailed` -- nothing was looked at (see the class). The caller records
      a failed check, not an expired session.

    The account label is cosmetic; failing to read it is logged and gives `None`.
    """
    try:
        result = executor.execute(BrowserAction("navigate", {"url": portal.policy.start_url}))
    except Exception as exc:
        log.warning("%s: start page navigation raised", portal.slug, exc_info=True)
        raise SignInCheckFailed(
            f"could not open {portal.policy.start_url}: {type(exc).__name__}: {str(exc)[:300]}"
        ) from exc
    if result.is_error:
        detail = str(result.content)[:300]
        log.warning("%s: start page did not load: %s", portal.slug, detail)
        raise SignInCheckFailed(f"could not open {portal.policy.start_url}: {detail}")
    page = getattr(executor, "page", None)
    if page is None:
        raise SignInCheckFailed(
            f"{type(executor).__name__} exposes no Playwright page to check the sign-in on"
        )
    try:
        signed_in = portal.is_signed_in(page)
    except PortalNeedsHuman:
        raise
    except Exception as exc:
        log.warning("%s: is_signed_in raised", portal.slug, exc_info=True)
        raise SignInCheckFailed(
            f"{portal.label}: the sign-in check failed: {type(exc).__name__}: {str(exc)[:300]}"
        ) from exc
    if not signed_in:
        log.info("%s: the portal reports signed out", portal.slug)
        return False, None
    try:
        label = portal.account_label(page)
    except PortalNeedsHuman:
        raise
    except Exception:
        log.warning("%s: account label unreadable", portal.slug, exc_info=True)
        label = None
    return True, label


__all__ = [
    "SessionKeyMissing",
    "SignInCheckFailed",
    "browser_data_dir",
    "check_signed_in",
    "decrypt_state",
    "encrypt_state",
    "has_display",
    "open_supplier_browser",
    "profile_dir_for",
    "profile_dir_name",
]
