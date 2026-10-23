"""Runtime settings. One home for every number."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = "postgresql+psycopg://localhost:5432/ezchain"
DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
MIN_PROD_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EZCHAIN_", env_file=".env", extra="ignore")

    app_name: str = "ezchain-api"
    environment: Literal["dev", "test", "prod", "desktop"] = "dev"
    database_url: str = DEFAULT_DATABASE_URL
    # Dev/test convenience only. Prod refuses this flag (see ``_validate_production``).
    auto_create_schema: bool = True
    log_level: str = "INFO"

    # Mutations are authenticated with a bearer token. Reads stay open so the UI and
    # health probes do not need a secret. In dev/test an unset token keeps mutations
    # available with a warning; prod refuses to start without one.
    auth_enabled: bool = True
    api_token: SecretStr | None = None

    data_mode: Literal["live", "fixture"] = "live"
    engine_inline: bool = True
    engine_interval_seconds: int = 30
    # Seed for the persisted operator kill switch when ``system_flags`` has no row yet.
    engine_kill_switch: bool = False

    market_scan_inline: bool = True
    market_scan_interval_seconds: int = Field(default=90, ge=30)
    market_scan_limit: int = Field(default=200, ge=1, le=500)

    paper_starting_cash_usd: Decimal = Field(default=Decimal("10000"))
    paper_fee_bps: int = 30
    max_order_usd: Decimal = Field(default=Decimal("1000"))
    max_position_usd: Decimal = Field(default=Decimal("2500"))
    max_open_positions: int = 10
    max_daily_loss_usd: Decimal = Field(default=Decimal("500"))
    max_quote_age_seconds: int = 60
    min_pair_age_minutes: int = 10
    min_liquidity_usd: Decimal = Field(default=Decimal("10000"))
    paper_mark_max_order_usd: Decimal = Field(default=Decimal("250"))
    # Execution-cost ceiling for a new position, as a ratio (0.05 = 5%). Applies to
    # executable quotes only; a paper_mark reports no impact and a sell is never blocked.
    max_price_impact_pct: Decimal = Field(default=Decimal("0.05"), ge=0, le=1)

    cors_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        """Refuse an unsafe production boot, naming the exact configuration key."""
        if self.environment != "prod":
            return self

        if not self.auth_enabled:
            raise ValueError("EZCHAIN_AUTH_ENABLED must be true in prod")
        token = None if self.api_token is None else self.api_token.get_secret_value()
        if token is None or len(token) < MIN_PROD_TOKEN_LENGTH:
            raise ValueError(
                f"EZCHAIN_API_TOKEN must be set and at least {MIN_PROD_TOKEN_LENGTH} "
                "characters in prod"
            )
        if not token.isascii():
            raise ValueError("EZCHAIN_API_TOKEN must be ASCII in prod")
        if self.auto_create_schema:
            raise ValueError("EZCHAIN_AUTO_CREATE_SCHEMA must be false in prod")
        if self.database_url == DEFAULT_DATABASE_URL:
            raise ValueError("EZCHAIN_DATABASE_URL must not use the local development default")
        try:
            password = make_url(self.database_url).password
        except Exception as exc:  # noqa: BLE001 - any parse failure is a bad production URL
            raise ValueError("EZCHAIN_DATABASE_URL must be a valid database URL") from exc
        if not password:
            raise ValueError("EZCHAIN_DATABASE_URL must include a password in prod")
        if self.data_mode == "fixture":
            raise ValueError("EZCHAIN_DATA_MODE must not be fixture in prod")
        if "*" in self.cors_origins:
            raise ValueError("EZCHAIN_CORS_ORIGINS must not contain '*' in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
