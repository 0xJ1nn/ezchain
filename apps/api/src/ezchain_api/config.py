"""Runtime settings. One home for every number."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from ezchain_core import TradeSizeLimits, TradeSizePolicy, validate_trade_policy

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
    # Scoped agent keys. Reads stay open by default for the self-hosted UI; a hosted
    # deployment that serves agents should set this true and hand each agent a key.
    require_auth_for_reads: bool = False
    api_key_pepper: SecretStr | None = None
    agent_key_default_ttl_days: int = Field(default=90, ge=1, le=3650)

    data_mode: Literal["live", "fixture"] = "live"
    # Server-derived execution mode. Live is refused until the executor exists; a client
    # can never request a mode on an order or a tool call.
    execution_mode: Literal["paper", "live"] = "paper"
    engine_inline: bool = True
    engine_interval_seconds: int = 30
    # Seed for the persisted operator kill switch when ``system_flags`` has no row yet.
    engine_kill_switch: bool = False

    # External strategy plugins. Paths are directories containing a manifest or child
    # packages with one; installed entry points are discovered in addition.
    plugin_paths: str = ""
    plugin_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)
    plugin_memory_mb: int = Field(default=512, ge=64, le=8192)
    plugin_cpu_seconds: int = Field(default=10, ge=1, le=120)

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
    # Server-derived trade sizing. The four top-level fields are the workspace envelope;
    # each profile may only tighten it (validated at boot and on every settings write).
    # Defaults are conservative and preserve the existing manual path: the default mid
    # profile resolves to $150 at $10k equity, while the existing $1000 order gate and
    # the $250 paper-mark gate still apply unchanged.
    trade_default_profile: Literal["low", "mid", "high"] = "mid"
    trade_min_amount_usd: Decimal = Field(default=Decimal("10"), ge=0)
    trade_min_pct_equity: Decimal = Field(default=Decimal("0.10"), ge=0, le=100)
    trade_max_amount_usd: Decimal = Field(default=Decimal("1000"), ge=0)
    trade_max_pct_equity: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    trade_low_min_amount_usd: Decimal = Field(default=Decimal("10"), ge=0)
    trade_low_min_pct_equity: Decimal = Field(default=Decimal("0.10"), ge=0, le=100)
    trade_low_max_amount_usd: Decimal = Field(default=Decimal("50"), ge=0)
    trade_low_max_pct_equity: Decimal = Field(default=Decimal("1"), ge=0, le=100)
    trade_mid_min_amount_usd: Decimal = Field(default=Decimal("10"), ge=0)
    trade_mid_min_pct_equity: Decimal = Field(default=Decimal("0.10"), ge=0, le=100)
    trade_mid_max_amount_usd: Decimal = Field(default=Decimal("150"), ge=0)
    trade_mid_max_pct_equity: Decimal = Field(default=Decimal("2"), ge=0, le=100)
    trade_high_min_amount_usd: Decimal = Field(default=Decimal("10"), ge=0)
    trade_high_min_pct_equity: Decimal = Field(default=Decimal("0.10"), ge=0, le=100)
    trade_high_max_amount_usd: Decimal = Field(default=Decimal("500"), ge=0)
    trade_high_max_pct_equity: Decimal = Field(default=Decimal("5"), ge=0, le=100)

    # Paper-to-live promotion gate (ADR-0009). Computed, never auto-promoted.
    paper_gate_min_days: int = Field(default=14, ge=1, le=365)
    paper_gate_min_intents: int = Field(default=100, ge=1)
    paper_gate_min_fills: int = Field(default=20, ge=1)
    paper_gate_require_backtest: bool = True

    cors_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))

    @field_validator("api_token", "api_key_pepper", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        """An empty env var means "unset", matching the documented dev fallback."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_trade_policy_defaults(self) -> Settings:
        errors = validate_trade_policy(self.trade_size_policy)
        if errors:
            raise ValueError("invalid trade sizing defaults: " + ", ".join(errors))
        return self

    @property
    def trade_size_policy(self) -> TradeSizePolicy:
        """The config-derived policy. Persisted settings replace it field-for-field."""
        return TradeSizePolicy(
            limits=TradeSizeLimits(
                min_amount_usd=self.trade_min_amount_usd,
                min_pct_equity=self.trade_min_pct_equity,
                max_amount_usd=self.trade_max_amount_usd,
                max_pct_equity=self.trade_max_pct_equity,
            ),
            profiles={
                "low": TradeSizeLimits(
                    min_amount_usd=self.trade_low_min_amount_usd,
                    min_pct_equity=self.trade_low_min_pct_equity,
                    max_amount_usd=self.trade_low_max_amount_usd,
                    max_pct_equity=self.trade_low_max_pct_equity,
                ),
                "mid": TradeSizeLimits(
                    min_amount_usd=self.trade_mid_min_amount_usd,
                    min_pct_equity=self.trade_mid_min_pct_equity,
                    max_amount_usd=self.trade_mid_max_amount_usd,
                    max_pct_equity=self.trade_mid_max_pct_equity,
                ),
                "high": TradeSizeLimits(
                    min_amount_usd=self.trade_high_min_amount_usd,
                    min_pct_equity=self.trade_high_min_pct_equity,
                    max_amount_usd=self.trade_high_max_amount_usd,
                    max_pct_equity=self.trade_high_max_pct_equity,
                ),
            },
            default_profile=self.trade_default_profile,
        )

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        """Refuse an unsafe boot, naming the exact configuration key."""
        if self.execution_mode == "live":
            raise ValueError(
                "EZCHAIN_EXECUTION_MODE=live is not available: the live executor is not shipped"
            )
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

    @property
    def plugin_path_list(self) -> list[str]:
        return [item.strip() for item in self.plugin_paths.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
