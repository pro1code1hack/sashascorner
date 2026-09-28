"""Wallet settings, prefix `CAFEOPS_WALLET_` (docs/loyalty/CONTRACT.md §7).

Separate from `cafeops.config.Settings` so the wallet can be configured, rotated and
diagnosed (`cafeops wallet doctor`) without touching the ops settings, and so a missing
certificate never stops the rest of the app from starting.

"Configured" means every file the signer needs is *present on disk*, not merely named:
a path set to a file that is not there (a volume not mounted, a typo) would otherwise
advertise an "Add to Apple Wallet" button that 503s on every tap.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cafeops.config import REPO_ROOT

#: The public site's domain (CONTRACT §0). Used only when neither this module's
#: `public_url` nor the loyalty `loyalty_public_url` is set.
DEFAULT_PUBLIC_URL = "https://sashascorner.co.uk"

#: Pass artwork committed to the repo (`cafeops wallet assets` regenerates it).
ASSETS_DIR = REPO_ROOT / "assets" / "pass"


class WalletNotConfigured(RuntimeError):
    """The credentials for this wallet are not set up. The API maps it to 503."""


class WalletSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="CAFEOPS_WALLET_"
    )

    # --- Apple PassKit ------------------------------------------------------
    #: e.g. "pass.uk.co.sashascorner.rewards" -- the Pass Type ID registered in the
    #: Apple Developer account. Also the APNs topic.
    apple_pass_type_id: str | None = None
    #: The 10-character Team ID of the organisation account.
    apple_team_id: str | None = None
    #: Pass Type ID certificate, PEM. Signs passes AND authenticates APNs pushes.
    apple_cert_path: Path | None = None
    #: Its private key, PEM (may be encrypted; then set the password).
    apple_key_path: Path | None = None
    apple_key_password: str | None = None
    #: Apple WWDR intermediate (G4), PEM or DER. Wallet rejects a signature without it.
    apple_wwdr_path: Path | None = None
    #: Development builds of iOS register with the sandbox gateway; production
    #: devices never do. Default production -- this is a live café.
    apple_apns_use_sandbox: bool = False

    # --- Google Wallet ------------------------------------------------------
    #: Numeric issuer ID from the Google Pay & Wallet Console.
    google_issuer_id: str | None = None
    #: Service-account JSON key with the Wallet Object Issuer role on that issuer.
    google_service_account_path: Path | None = None
    #: Class id becomes "<issuer>.<suffix>". Changing it creates a NEW class, which
    #: Google reviews again -- keep it stable once the class is approved.
    google_class_suffix: str = "stamp"

    # --- shared -------------------------------------------------------------
    #: Public origin of the site. webServiceURL, image URIs and JWT origins derive
    #: from it. Defaults to CAFEOPS_LOYALTY_PUBLIC_URL, then the site domain.
    public_url: str | None = None

    @model_validator(mode="after")
    def _default_public_url(self) -> WalletSettings:
        if not self.public_url:
            # Read lazily: the loyalty setting is Agent A's and may not exist yet on
            # an older checkout; the wallet must not refuse to import because of it.
            from cafeops.config import settings

            self.public_url = getattr(settings, "loyalty_public_url", None) or DEFAULT_PUBLIC_URL
        self.public_url = self.public_url.rstrip("/")
        return self

    # --- derived ------------------------------------------------------------
    @property
    def base_url(self) -> str:
        assert self.public_url is not None  # set by the validator
        return self.public_url

    def apple_missing(self) -> list[str]:
        """Env vars (or files) that stop Apple passes, in the order to fix them."""
        missing: list[str] = []
        for name in ("apple_pass_type_id", "apple_team_id"):
            if not getattr(self, name):
                missing.append(f"CAFEOPS_WALLET_{name.upper()}")
        for name in ("apple_cert_path", "apple_key_path", "apple_wwdr_path"):
            path: Path | None = getattr(self, name)
            env = f"CAFEOPS_WALLET_{name.upper()}"
            if path is None:
                missing.append(env)
            elif not path.is_file():
                missing.append(f"{env} (file not found: {path})")
        return missing

    def google_missing(self) -> list[str]:
        missing: list[str] = []
        if not self.google_issuer_id:
            missing.append("CAFEOPS_WALLET_GOOGLE_ISSUER_ID")
        path = self.google_service_account_path
        env = "CAFEOPS_WALLET_GOOGLE_SERVICE_ACCOUNT_PATH"
        if path is None:
            missing.append(env)
        elif not path.is_file():
            missing.append(f"{env} (file not found: {path})")
        return missing

    @property
    def apple_configured(self) -> bool:
        return not self.apple_missing()

    @property
    def google_configured(self) -> bool:
        return not self.google_missing()

    @property
    def google_class_id(self) -> str:
        return f"{self.google_issuer_id}.{self.google_class_suffix}"

    def google_class_id_for(self, program_slug: str) -> str:
        """Phase 3: one LoyaltyClass per programme. The main card keeps the phase-1 id,
        so a class already approved by Google stays approved."""
        if program_slug == "stamp":
            return self.google_class_id
        return f"{self.google_issuer_id}.{self.google_class_suffix}-{program_slug}"

    def apple_web_service_url(self) -> str:
        # Apple appends "/v1/..." itself; the router is mounted at /wallet/apple/v1.
        return f"{self.base_url}/wallet/apple"

    def asset_url(self, name: str) -> str:
        return f"{self.base_url}/api/loyalty/pass-assets/{name}"

    def strip_url(
        self,
        stamps: int,
        required: int,
        scale: int = 3,
        *,
        reward: bool = False,
        keys: tuple[str, ...] | None = None,
    ) -> str:
        # `v` is the sticker-art hash: strips are cached `immutable` and Google only
        # re-fetches a heroImage whose URL changed, so new art must mean a new URL.
        from cafeops.integrations.wallet.strips import strip_version

        query = f"v={strip_version()}" + ("&r=1" if reward else "")
        if keys:
            # The card's own stickers, BACKOFFICE-V2 §2: a different set is a different URL.
            query += "&s=" + ".".join(keys)
        return f"{self.base_url}/api/loyalty/strip/{stamps}-{required}@{scale}x.png?{query}"


wallet_settings = WalletSettings()

__all__ = ["ASSETS_DIR", "WalletNotConfigured", "WalletSettings", "wallet_settings"]
