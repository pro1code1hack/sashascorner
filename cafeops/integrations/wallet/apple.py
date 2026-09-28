"""Apple Wallet: build and sign the `.pkpass` for one card.

A .pkpass is a zip of `pass.json`, the images, `manifest.json` (SHA-1 of every other
file -- Apple's format, not our choice) and `signature`: a detached PKCS#7 over the
manifest, signed with the Pass Type ID certificate and carrying Apple's WWDR
intermediate. Wallet refuses the whole pass if any hash or the chain is off, and says
only "cannot be added", so `cafeops wallet preview` exists to look inside one.

**Lock-screen messages.** Wallet shows a notification when a field that has a
`changeMessage` gets a new value. The spec wants "You've got 6 stamps" and "Your free
drink is ready" -- but putting `changeMessage` on the stamp header would announce
"You've got 0/8 stamps" at the exact moment a reward is issued (the count carries over
to 0). So the text comes from the outbox instead: `latest_message` (the newest message
`services/loyalty` enqueued for this card) is the value of a back field whose
changeMessage is just "%@". A silent refresh keeps the previous message, the value does
not change, and Wallet stays quiet -- which is exactly what an undo or a redeem wants.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.serialization import pkcs7

from cafeops.integrations.wallet import content
from cafeops.integrations.wallet.assets import APPLE_FILES, pass_asset
from cafeops.integrations.wallet.cafe import cafe
from cafeops.integrations.wallet.config import (
    WalletNotConfigured,
    WalletSettings,
    wallet_settings,
)
from cafeops.integrations.wallet.strips import (
    BACKGROUND,
    FOREGROUND,
    LABEL,
    SCALES,
    strip_filename,
    strip_png,
)

if TYPE_CHECKING:
    from cafeops.services.loyalty.card_view import CardView

PKPASS_MIME = "application/vnd.apple.pkpass"
RELEVANT_TEXT = "Your stamp card is ready at Sasha's Corner"


def _rgb(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    return f"rgb({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)})"


def pass_json(
    view: CardView,
    *,
    latest_message: str | None = None,
    cfg: WalletSettings | None = None,
) -> dict[str, Any]:
    """pass.json for the card. Pure apart from reading settings and café facts."""
    cfg = cfg or wallet_settings
    place = cafe()
    site = cfg.base_url
    member_since = f"{view.member_since.day} {view.member_since:%B %Y}"
    back: list[dict[str, Any]] = [
        {
            # See the module docstring: this field carries the lock-screen message.
            "key": "news",
            "label": "Latest",
            "value": latest_message or (content.DELETED if view.voided else content.WELCOME),
            "changeMessage": "%@",
        },
    ]
    rewards = content.reward_lines(view)
    if rewards:
        back.append({"key": "rewards", "label": "Your rewards", "value": "\n".join(rewards)})
    back += [
        {"key": "rules", "label": "How it works", "value": content.rules_text(view)},
        {"key": "hours", "label": "Opening hours", "value": place.hours},
        {"key": "address", "label": "Find us", "value": place.address},
        {
            "key": "phone",
            "label": "Phone",
            "value": place.phone,
            "dataDetectorTypes": ["PKDataDetectorTypePhoneNumber"],
        },
        {
            "key": "website",
            "label": "Website",
            "value": site,
            "dataDetectorTypes": ["PKDataDetectorTypeLink"],
        },
        {
            "key": "privacy",
            "label": "Privacy",
            "attributedValue": f"<a href='{site}/privacy'>How we use your details</a>",
            "value": f"{site}/privacy",
        },
    ]
    body: dict[str, Any] = {
        "formatVersion": 1,
        "passTypeIdentifier": cfg.apple_pass_type_id,
        "serialNumber": view.card_id,
        "teamIdentifier": cfg.apple_team_id,
        "organizationName": "Sasha's Corner",
        "description": (
            f"{view.program_name} stamp card"
            if content.is_main(view)
            else f"{view.program_name} card, Sasha's Corner"
        ),
        "logoText": "Sasha's Corner",
        "foregroundColor": _rgb(FOREGROUND),
        "labelColor": _rgb(LABEL),
        "backgroundColor": _rgb(BACKGROUND),
        # A stamp card is personal; sharing it would share the stamps.
        "sharingProhibited": True,
        "webServiceURL": cfg.apple_web_service_url(),
        "authenticationToken": view.auth_token,
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": view.qr_payload,
                "messageEncoding": "iso-8859-1",
            }
        ],
        # Spec phase 2 "location notification near 23 Commercial Street": Wallet shows
        # the pass on the lock screen near these coordinates. No push, no tracking --
        # the device decides, and we never learn where anyone was.
        "locations": [
            {"latitude": place.lat, "longitude": place.lng, "relevantText": RELEVANT_TEXT}
        ],
        "storeCard": {
            "headerFields": [
                {
                    "key": "stamps",
                    "label": content.unit_label(view),
                    "value": content.stamps_value(view),
                }
            ],
            "primaryFields": [
                {"key": "reward", "label": view.program_name, "value": content.headline(view)}
            ],
            "secondaryFields": [
                {"key": "name", "label": "Member", "value": view.first_name or "Member"},
                {"key": "since", "label": "Member since", "value": member_since},
            ],
            "backFields": back,
        },
    }
    if view.voided:
        body["voided"] = True
    return body


def _bundle_files(view: CardView, latest_message: str | None) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "pass.json": json.dumps(
            pass_json(view, latest_message=latest_message), ensure_ascii=False, indent=2
        ).encode("utf-8"),
    }
    for name in APPLE_FILES:
        files[name] = pass_asset(name)
    filled, slots = content.strip_counts(view)
    for scale in SCALES:
        files[strip_filename(scale)] = strip_png(
            filled,
            slots,
            scale,
            reward=content.stamp_reward_ready(view),
            keys=content.strip_stickers(view),
        )
    return files


def manifest(files: dict[str, bytes]) -> bytes:
    return json.dumps(
        {name: hashlib.sha1(data).hexdigest() for name, data in sorted(files.items())},
        indent=2,
    ).encode("utf-8")


# --- signing --------------------------------------------------------------


@dataclass(frozen=True)
class SigningMaterial:
    cert: x509.Certificate
    key: PrivateKeyTypes
    wwdr: x509.Certificate


def _load_cert(path: Path) -> x509.Certificate:
    data = path.read_bytes()
    # Apple hands out DER (.cer); people convert to PEM. Accept both.
    if b"-----BEGIN" in data:
        return x509.load_pem_x509_certificate(data)
    return x509.load_der_x509_certificate(data)


@lru_cache(maxsize=4)
def _load_material(
    cert_path: Path,
    key_path: Path,
    wwdr_path: Path,
    password: str | None,
    _mtimes: tuple[float, ...],
) -> SigningMaterial:
    key_data = key_path.read_bytes()
    pw = password.encode() if password else None
    key = (
        serialization.load_pem_private_key(key_data, password=pw)
        if b"-----BEGIN" in key_data
        else serialization.load_der_private_key(key_data, password=pw)
    )
    return SigningMaterial(cert=_load_cert(cert_path), key=key, wwdr=_load_cert(wwdr_path))


def signing_material(cfg: WalletSettings | None = None) -> SigningMaterial:
    """Load (and cache, keyed on file mtimes so a renewed cert is picked up) the keys."""
    cfg = cfg or wallet_settings
    if not cfg.apple_configured:
        raise WalletNotConfigured("apple not configured: " + ", ".join(cfg.apple_missing()))
    assert cfg.apple_cert_path and cfg.apple_key_path and cfg.apple_wwdr_path
    paths = (cfg.apple_cert_path, cfg.apple_key_path, cfg.apple_wwdr_path)
    return _load_material(*paths, cfg.apple_key_password, tuple(p.stat().st_mtime for p in paths))


def sign_manifest(manifest_bytes: bytes, material: SigningMaterial) -> bytes:
    """Detached PKCS#7 (DER) over the manifest, with the WWDR intermediate included.

    Binary so the bytes are signed as-is (no MIME canonicalisation). The builder adds
    the signing-time attribute Wallet expects.
    """
    key: Any = material.key  # the builder narrows to RSA/EC keys at runtime
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(material.cert, key, hashes.SHA256())
        .add_certificate(material.wwdr)
        .sign(
            serialization.Encoding.DER,
            [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary],
        )
    )


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def build_pkpass(view: CardView, *, latest_message: str | None = None) -> bytes:
    """The signed .pkpass. Raises `WalletNotConfigured` without certificates.

    `latest_message` (optional, a compatible addition to CONTRACT §3): the newest
    lock-screen text for this card; the web service passes it, the download route
    need not.
    """
    material = signing_material()
    files = _bundle_files(view, latest_message)
    files["manifest.json"] = manifest(files)
    files["signature"] = sign_manifest(files["manifest.json"], material)
    return _zip(files)


def write_unsigned_preview(
    view: CardView, out_dir: Path, *, latest_message: str | None = None
) -> list[Path]:
    """Write the unzipped pass folder (no signature) for inspection in dev.

    Works without any Apple configuration; the identifiers are placeholders then.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    files = _bundle_files(view, latest_message)
    files["manifest.json"] = manifest(files)
    written = []
    for name, data in files.items():
        path = out_dir / name
        path.write_bytes(data)
        written.append(path)
    return written


__all__ = [
    "PKPASS_MIME",
    "build_pkpass",
    "manifest",
    "pass_json",
    "sign_manifest",
    "signing_material",
    "write_unsigned_preview",
]
