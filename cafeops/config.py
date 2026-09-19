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
