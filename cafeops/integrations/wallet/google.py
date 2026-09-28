"""Google Wallet: one LoyaltyClass per programme, one LoyaltyObject per card.

Phase 3: the main card keeps its phase-1 class (`<issuer>.<suffix>`); every other
programme has `<issuer>.<suffix>-<slug>`, named after the programme. The save link
carries a club's class inside the JWT, so Google creates it on the first save and a new
programme needs no console step before its first card can be saved (the main class
still goes through `google-class-sync`, as before).

Plain REST over httpx with a service-account token, not the google-api-python-client
SDK: four endpoints do not justify a dependency tree that size, and the auth is one
signed JWT exchanged for a bearer token (RFC 7523).

"Add to Google Wallet" is a link carrying a JWT signed with the service-account key
that *contains the whole object*, so the object is created on Google's side the moment
the customer saves it -- no insert call from us, and a card that is never saved never
exists at Google. Updates are a PATCH on the object; before the customer saves, that
PATCH answers 404, which `sync.py` treats as "nothing to update", not a failure.

The class must exist (and, for customers outside the issuer's test accounts, be
approved by Google) before any save link works: `cafeops wallet google-class-sync`.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import jwt

from cafeops.integrations.wallet import content
from cafeops.integrations.wallet.cafe import cafe
from cafeops.integrations.wallet.config import (
    WalletNotConfigured,
    WalletSettings,
    wallet_settings,
)
from cafeops.integrations.wallet.strips import BACKGROUND

if TYPE_CHECKING:
    from cafeops.services.loyalty.card_view import CardView

API = "https://walletobjects.googleapis.com/walletobjects/v1"
SCOPE = "https://www.googleapis.com/auth/wallet_object.issuer"
SAVE_BASE = "https://pay.google.com/gp/v/save/"
LANG = "en-GB"
PROGRAM_NAME = "Sasha's Corner Rewards"


class GoogleWalletError(RuntimeError):
    """A Google API call failed. `status` is the HTTP status (0 = transport)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"google wallet {status}: {detail}")
        self.status = status


def _text(value: str) -> dict[str, Any]:
    return {"defaultValue": {"language": LANG, "value": value}}


# --- service account --------------------------------------------------------


@dataclass(frozen=True)
class ServiceAccount:
    client_email: str
    private_key: str
    private_key_id: str | None
    token_uri: str


@lru_cache(maxsize=2)
def _load_sa(path: Path, _mtime: float) -> ServiceAccount:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ServiceAccount(
        client_email=raw["client_email"],
        private_key=raw["private_key"],
        private_key_id=raw.get("private_key_id"),
        token_uri=raw.get("token_uri") or "https://oauth2.googleapis.com/token",
    )


def service_account(cfg: WalletSettings | None = None) -> ServiceAccount:
    cfg = cfg or wallet_settings
    if not cfg.google_configured:
        raise WalletNotConfigured("google not configured: " + ", ".join(cfg.google_missing()))
    path = cfg.google_service_account_path
    assert path is not None
    return _load_sa(path, path.stat().st_mtime)


def _sign(claims: dict[str, Any], sa: ServiceAccount) -> str:
    headers = {"kid": sa.private_key_id} if sa.private_key_id else None
    return jwt.encode(claims, sa.private_key, algorithm="RS256", headers=headers)


_token_lock = threading.Lock()
_token: tuple[str, float] | None = None


def access_token(cfg: WalletSettings | None = None) -> str:
    """OAuth bearer token via the JWT-bearer grant, cached until a minute before expiry."""
    global _token
    sa = service_account(cfg)
    with _token_lock:
        if _token is not None and _token[1] > time.time() + 60:
            return _token[0]
        now = int(time.time())
        assertion = _sign(
            {
                "iss": sa.client_email,
                "scope": SCOPE,
                "aud": sa.token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            sa,
        )
        try:
            resp = httpx.post(
                sa.token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise GoogleWalletError(0, f"token: {exc}") from exc
        if resp.status_code != 200:
            raise GoogleWalletError(resp.status_code, f"token: {resp.text[:300]}")
        body = resp.json()
        _token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return _token[0]


def _request(method: str, path: str, *, json_body: Any = None) -> httpx.Response:
    try:
        return httpx.request(
            method,
            f"{API}{path}",
            json=json_body,
            headers={"Authorization": f"Bearer {access_token()}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise GoogleWalletError(0, f"{method} {path}: {exc}") from exc


# --- class and object bodies -----------------------------------------------


def object_id(card_id: str, cfg: WalletSettings | None = None) -> str:
    cfg = cfg or wallet_settings
    return f"{cfg.google_issuer_id}.{card_id}"


def class_id(view: CardView | None, cfg: WalletSettings) -> str:
    return cfg.google_class_id_for(view.program_slug) if view is not None else cfg.google_class_id


def loyalty_class(
    cfg: WalletSettings | None = None, view: CardView | None = None
) -> dict[str, Any]:
    """The class for the main card (no `view`) or for `view`'s programme."""
    cfg = cfg or wallet_settings
    place = cafe()
    main = view is None or content.is_main(view)
    rules = (
        "One stamp for every hot or cold drink, up to 3 per visit. Collect 8 and "
        "your next drink is on us. Stamps above 8 carry over."
    )
    if view is not None and not main:
        rules = content.rules_text(view)
    return {
        "id": class_id(view, cfg),
        "issuerName": "Sasha's Corner",
        "programName": PROGRAM_NAME if main or view is None else view.program_name,
        "programLogo": {
            "sourceUri": {"uri": cfg.asset_url("google-logo.png")},
            "contentDescription": _text("Sasha's Corner line-drawing of a coffee cup"),
        },
        "hexBackgroundColor": BACKGROUND,
        "countryCode": "GB",
        # A class is created or changed "under review"; Google approves it once.
        "reviewStatus": "UNDER_REVIEW",
        "multipleDevicesAndHoldersAllowedStatus": "ONE_USER_ALL_DEVICES",
        "localizedRewardsTier": _text(
            "Points card" if view is not None and content.is_points(view) else "Stamp card"
        ),
        "textModulesData": [
            {"id": "rules", "header": "How it works", "body": rules},
            {"id": "hours", "header": "Opening hours", "body": place.hours},
        ],
        "linksModuleData": {
            "uris": [
                {"id": "site", "uri": cfg.base_url, "description": "Website"},
                {
                    "id": "phone",
                    "uri": "tel:+44" + place.phone.replace(" ", "").lstrip("0"),
                    "description": f"Call {place.phone}",
                },
                {"id": "privacy", "uri": f"{cfg.base_url}/privacy", "description": "Privacy"},
            ]
        },
    }


def loyalty_object(view: CardView, cfg: WalletSettings | None = None) -> dict[str, Any]:
    cfg = cfg or wallet_settings
    modules = [{"id": "reward", "header": "Reward", "body": content.headline(view)}]
    # Every reward beyond the headline, or one whose expiry the headline does not show.
    extra = [line for line in content.reward_lines(view) if line != content.headline(view)]
    for i, line in enumerate(extra):
        modules.append({"id": f"r{i}", "header": "Ready to redeem", "body": line})
    modules.append({"id": "rules", "header": "How it works", "body": content.rules_text(view)})
    since = f"{view.member_since.day} {view.member_since:%B %Y}"
    filled, slots = content.strip_counts(view)
    return {
        "id": object_id(view.card_id, cfg),
        "classId": class_id(view, cfg),
        "state": "INACTIVE" if view.voided else "ACTIVE",
        "accountName": view.first_name or "Member",
        "loyaltyPoints": {
            "label": content.unit_label(view),
            "balance": {"int": view.stamps_current},
        },
        "barcode": {"type": "QR_CODE", "value": view.qr_payload},
        "heroImage": {
            "sourceUri": {
                "uri": cfg.strip_url(
                    filled,
                    slots,
                    3,
                    reward=content.stamp_reward_ready(view),
                    keys=content.strip_stickers(view),
                ),
            },
            "contentDescription": _text(
                f"{view.stamps_current} of {view.stamps_required} "
                f"{content.unit_label(view).lower()} collected"
            ),
        },
        "hexBackgroundColor": BACKGROUND,
        "textModulesData": modules,
        "infoModuleData": {
            "labelValueRows": [{"columns": [{"label": "Member since", "value": since}]}]
        },
    }


# --- public API -------------------------------------------------------------


def save_url(view: CardView) -> str:
    """ "Add to Google Wallet" link for this card. Raises `WalletNotConfigured`.

    Pure computation (no network): the object travels inside the JWT.
    """
    sa = service_account()
    cfg = wallet_settings
    claims = {
        "iss": sa.client_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        # The site's origin, so the button works when rendered from our pages.
        "origins": [cfg.base_url],
        "payload": _save_payload(view, cfg),
    }
    return SAVE_BASE + _sign(claims, sa)


def _save_payload(view: CardView, cfg: WalletSettings) -> dict[str, Any]:
    payload: dict[str, Any] = {"loyaltyObjects": [loyalty_object(view, cfg)]}
    if not content.is_main(view):
        # A club's class travels with its first save (see the module docstring).
        payload["loyaltyClasses"] = [loyalty_class(cfg, view)]
    return payload


def upsert_class() -> str:
    """Create the LoyaltyClass, or bring an existing one up to date. Returns 'created' |
    'updated'. Updating an approved class may send it back to review -- run on purpose."""
    body = loyalty_class()
    cid = body["id"]
    got = _request("GET", f"/loyaltyClass/{cid}")
    if got.status_code == 404:
        resp = _request("POST", "/loyaltyClass", json_body=body)
        action = "created"
    elif got.status_code == 200:
        resp = _request("PATCH", f"/loyaltyClass/{cid}", json_body=body)
        action = "updated"
    else:
        raise GoogleWalletError(got.status_code, got.text[:300])
    if resp.status_code not in (200, 201):
        raise GoogleWalletError(resp.status_code, resp.text[:300])
    return action


def patch_object(view: CardView) -> bool:
    """PATCH the card's object. False when Google has no such object (never saved)."""
    body = loyalty_object(view)
    resp = _request("PATCH", f"/loyaltyObject/{body['id']}", json_body=body)
    if resp.status_code == 404:
        return False
    if resp.status_code != 200:
        raise GoogleWalletError(resp.status_code, resp.text[:300])
    return True


def add_message(card_id: str, message: str, *, message_id: str) -> None:
    """Lock-screen notification on the saved pass (TEXT_AND_NOTIFY).

    Google rate-limits notifying messages per object (a few per day); a 429 surfaces as
    an error and `sync.py`'s backoff handles it. `message_id` makes a retry idempotent.
    """
    now = datetime.now(UTC)
    body = {
        "message": {
            "id": message_id,
            "header": "Sasha's Corner",
            "body": message,
            "messageType": "TEXT_AND_NOTIFY",
            "displayInterval": {
                "start": {"date": now.isoformat().replace("+00:00", "Z")},
                "end": {"date": (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")},
            },
        }
    }
    resp = _request("POST", f"/loyaltyObject/{object_id(card_id)}/addMessage", json_body=body)
    if resp.status_code == 409:
        return  # same message id already added by an earlier attempt
    if resp.status_code != 200:
        raise GoogleWalletError(resp.status_code, resp.text[:300])


__all__ = [
    "GoogleWalletError",
    "access_token",
    "add_message",
    "loyalty_class",
    "loyalty_object",
    "object_id",
    "patch_object",
    "save_url",
    "upsert_class",
]
