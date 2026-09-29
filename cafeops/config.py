"""Application configuration. No secrets in code -- everything via .env."""

from __future__ import annotations

import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="CAFEOPS_"
    )

    # --- database -----------------------------------------------------------
    # Swapping SQLite for Postgres later is this line plus an Alembic branch.
    database_url: str = f"sqlite+pysqlite:///{REPO_ROOT / 'cafeops.db'}"
    sql_echo: bool = False

    # --- locale -------------------------------------------------------------
    # Stored timestamps are always UTC. This is only for presentation and for
    # deciding which calendar day a sale belongs to.
    local_timezone: str = "Europe/London"

    # --- Lightspeed (Restaurant K-Series) -----------------------------------
    # Fixtures-first: absent credentials is a supported state, not an error.
    lightspeed_base_url: str = "https://api.lsk.lightspeed.app/v3"
    lightspeed_client_id: str | None = None
    lightspeed_client_secret: str | None = None
    lightspeed_refresh_token: str | None = None
    lightspeed_business_id: str | None = None
    lightspeed_rate_limit_per_second: float = 5.0
    lightspeed_max_retries: int = 5

    # --- read-only API ------------------------------------------------------
    #: Single shared password (spec 10: no user management). The API FAILS CLOSED
    #: when unset -- every route but /api/health answers 503 rather than serving the
    #: café's costs and margins to anyone who finds the port.
    api_password: str | None = None

    # --- menu photos (recipes spec A5) --------------------------------------
    #: Where uploaded menu photos are written, as `<sha256>.<ext>`. Served read-only
    #: at `/media/<sha256>.<ext>` (by Caddy in production; content-addressed, so the
    #: cache header can be immutable). NOT inside the database directory: the web
    #: server gets read access to this and nothing else. In docker: `/media`.
    media_dir: Path = REPO_ROOT / "media"

    # --- telegram -----------------------------------------------------------
    telegram_bot_token: str | None = None
    telegram_owner_chat_id: int | None = None

    # --- seed / import ------------------------------------------------------
    finance_workbook_path: Path | None = None

    # --- forecasting knobs (spec section 3.3) -------------------------------
    ewma_alpha: float = Field(default=0.3, gt=0, le=1)
    ewma_window_days: int = 28
    dow_window_weeks: int = 8
    dow_factor_min: float = 0.5
    dow_factor_max: float = 2.0
    min_history_days: int = 14

    # --- labour and true margin (spec 5.6, answered in spec 15.7) -----------
    # GBP 14.50/hr LOADED -- wage plus employer NI, pension, holiday accrual --
    # confirmed with the owner. It lives here and never in `domain/`: the domain
    # takes it as an argument so it can be asked "and what if it were 15.50?", and
    # so a rate change is a config change rather than a code change.
    #
    # It is OPTIONAL, and that is deliberate. Set it to 0 or clear it and every
    # labour figure in the system becomes None rather than a flattering number
    # computed from nothing. A labour cost from a guessed rate is a guess wearing a
    # number's clothes, and once it is in a table nobody can tell it from a real one.
    loaded_hourly_rate_pence: int | None = 1450

    # --- channels: Deliveroo / Just Eat (spec 4.6) --------------------------
    # Partner APIs are gated to certified POS integrators, so there are two real
    # implementations and which one runs is THIS setting. `CSV` is the path that
    # works today (the owner has portal logins and exports by hand);
    # `BROWSER_AGENT` drives a browser and falls back to CSV when it breaks --
    # which spec 4.6 says to assume it will. Falling back is this line, not a
    # rewrite. `PARTNER_API` is not constructible and says so.
    channel_source: str = "CSV_UPLOAD"
    # Where hand-exported portal CSVs are dropped. Unset uses the shipped fixtures,
    # so a fresh checkout can exercise the whole path before anyone downloads
    # anything.
    channel_csv_dir: Path | None = None
    # Which browser driver backs BROWSER_AGENT. Unset means none is wired, which is
    # the normal state today: the source raises ChannelSourceUnavailable and the
    # sync job uses the CSV fallback. `fixture` runs the no-network driver against
    # the sample files so the path is demonstrable without touching a portal.
    channel_browser_driver: str | None = None

    # --- the bounded agent (spec 9) -----------------------------------------
    # Absent key is a SUPPORTED state, not an error: the narration tool falls back
    # to a deterministic template-built sentence rather than refusing to report.
    anthropic_api_key: str | None = None
    # Model id for narration. Kept in config because it is an operational choice
    # and belongs in an audit row (`agent_action_log.model`), not in a constant.
    agent_model: str = "claude-opus-5"
    agent_max_tokens: int = 1200
    # Hard ceiling on tool calls in one run. An agent that loops is an agent that
    # spends money, and spec 9 says anything spending money stops at a human.
    agent_max_tool_calls: int = 12

    # --- drift gate (spec section 3.2) --------------------------------------
    drift_auto_order_max_pct: float = 10.0
    drift_warn_max_pct: float = 15.0
    drift_consecutive_counts_required: int = 2

    # --- browser ordering agents (docs/agents/BROWSER-ORDERING.md) ----------
    # A separate process (`cafeops browser-worker`) is the only thing that opens a
    # browser. The API and the scheduler only queue `browser_job` rows. Off by
    # default: with it off, "Stage basket" is refused with a reason, not silently
    # ignored, and the scheduler queues nothing.
    browser_worker_enabled: bool = False
    # Persistent Chromium profiles, one directory per supplier. Unset resolves to
    # `<database directory>/browser`. Cookies live here, never in the database.
    browser_data_dir: Path | None = None
    browser_headless: bool = True
    # Fernet key (urlsafe base64, 32 bytes) protecting a Playwright storage state at
    # rest in `supplier_session.storage_state_enc`. Unset means import/export of a
    # sign-in is refused; the profile directory still works on its own.
    browser_session_key: str | None = None
    # The model that drives the browser toolset. Distinct from `agent_model` (narration)
    # because it is a different job with a different cost, and it is written to every
    # job row so a basket can be traced to the model that staged it.
    browser_model: str = "claude-opus-5-5"
    browser_max_tokens: int = 4096
    # Hard ceilings per job. A browser agent that loops spends money on tokens AND can
    # wander into a checkout; both stop here. Actions count scripted and model steps.
    browser_max_model_calls: int = 40
    browser_max_actions: int = 150
    browser_max_minutes: int = 12
    # Screenshots are resized to fit this on the long side before they go to the
    # model (vision tokens) and to media_asset (disk).
    browser_screenshot_max_px: int = 1280
    browser_worker_poll_seconds: int = 5
    # A RUNNING job whose heartbeat is older than this is treated as orphaned by a
    # dead worker and FAILED so the order page stops saying "staging".
    browser_heartbeat_stale_seconds: int = 120

    # --- Sasha's Corner Rewards (docs/loyalty/CONTRACT.md §7) ---------------
    #: The public site's origin. Recovery links, unsubscribe links and the web card URL
    #: staff show as a QR are built on it. No trailing slash.
    loyalty_public_url: str = "https://sashascorner.co.uk"
    #: HMAC key for the pass QR (`SC1:<card>:<hmac8>`), recovery codes and unsubscribe
    #: links. Unset derives a stable DEV key from the database URL and `cafeops doctor`
    #: warns: every printed QR is signed with it, so it must be set, and never changed,
    #: before the first real customer joins.
    loyalty_qr_key: str | None = None
    #: Outbound mail for recovery codes and campaign emails. Unset is supported: recovery
    #: then answers `ask_staff` and campaigns go to wallets only.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    #: SMS for recovery codes. Unset is supported, as above.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    #: Phase 3: stamp (or add points to) a member's cards from Lightspeed receipts that
    #: name their till customer, during `cafeops sync` / the daily sync and
    #: `cafeops loyalty pos-sync`. Off by default: until the till attaches customers to
    #: sales, staff scans are the only stamps (docs/loyalty/CONTRACT.md "Phase 3").
    loyalty_auto_stamp: bool = False
    #: A staff PURCHASE stamp on the same card within this many minutes either side of
    #: the receipt's close time means the visit was already stamped: the receipt earns
    #: nothing, so one coffee is never two stamps.
    loyalty_pos_dedupe_minutes: int = 30

    # --- the public website's admin (Website group in the back office) ------
    #: The site API (site/backend, `sashasite`). The back office forwards
    #: `/api/website/*` there after its own sign-in. In docker: http://site-api:8100.
    site_api_url: str = "http://127.0.0.1:8100"
    #: Shared secret sent as X-Site-Service-Key; the site accepts it in place of its
    #: own cookie session. One value in .env serves both apps (SITE_SERVICE_KEY).
    #: Unset: the Website screens say so and nothing is forwarded.
    site_service_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CAFEOPS_SITE_SERVICE_KEY", "SITE_SERVICE_KEY"),
    )
    #: The site's public origin, for "open the live page" links. Shared with the site.
    site_public_url: str = Field(
        default="https://sashascorner.co.uk",
        validation_alias=AliasChoices("CAFEOPS_SITE_PUBLIC_URL", "SITE_PUBLIC_URL"),
    )

    # --- Order online (docs/shop/CONTRACT.md §9) ------------------------------
    #: Stripe Checkout for "pay online". All three optional: without the secret key the
    #: shop reports `pay_online: false` whatever the admin setting says (§3.6).
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None
    #: Where the ordering app lives (`/order/...` links in Stripe redirects and
    #: notifications). Empty defaults to `site_public_url` (see `shop_url`).
    shop_public_url: str = Field(
        default="",
        validation_alias=AliasChoices("CAFEOPS_SHOP_PUBLIC_URL", "SHOP_PUBLIC_URL"),
    )

    #: K-Series Order & Pay (CONTRACT §3b, `integrations/pos/lightspeed.py`): the
    #: business LOCATION id (not the business id above) and the webhook endpoint id the
    #: Order & Pay API needs on every pushed order. Unset: the sink reports not
    #: configured and nothing is pushed.
    lightspeed_business_location_id: int | None = None
    lightspeed_online_order_endpoint_id: str | None = None
    #: The merchant-configured payment method code an already-paid online order is
    #: recorded under on the till ("ONLINE" is a placeholder to confirm in the back office).
    lightspeed_payment_method_code: str | None = None

    #: Web Push for order status (CONTRACT §3c). `cafeops shop vapid-keys` prints a pair;
    #: the subject is a `mailto:` the push services may contact. Unset: no push, the
    #: status page polls as before.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:hello@sashascorner.co.uk"

    @property
    def push_configured(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def shop_url(self) -> str:
        return (self.shop_public_url or self.site_public_url).rstrip("/")

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_owner_chat_id)

    @property
    def loyalty_key(self) -> bytes:
        """The QR/HMAC key. A derived dev key when unset -- see `loyalty_qr_key`."""
        if self.loyalty_qr_key:
            return self.loyalty_qr_key.encode("utf-8")
        return hashlib.sha256(f"cafeops-loyalty-dev:{self.database_url}".encode()).digest()

    @property
    def loyalty_key_is_dev(self) -> bool:
        return not self.loyalty_qr_key

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.local_timezone)

    @property
    def lightspeed_configured(self) -> bool:
        # All four: the client refuses to start with any one missing.
        return bool(
            self.lightspeed_client_id
            and self.lightspeed_client_secret
            and self.lightspeed_refresh_token
            and self.lightspeed_business_id
        )


settings = Settings()
