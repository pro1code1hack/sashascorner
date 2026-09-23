"""Application configuration. No secrets in code -- everything via .env."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
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

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.local_timezone)

    @property
    def lightspeed_configured(self) -> bool:
        return bool(self.lightspeed_client_id and self.lightspeed_client_secret)


settings = Settings()
