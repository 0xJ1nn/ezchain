"""Runtime settings. One home for every number."""

from __future__ import annotations

import ipaddress
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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
    # Scoped agent keys. Reads stay open by default for the self-hosted UI; a hosted
    # deployment that serves agents should set this true and hand each agent a key.
    require_auth_for_reads: bool = False
    api_key_pepper: SecretStr | None = None
    agent_key_default_ttl_days: int = Field(default=90, ge=1, le=3650)

    # User-supplied provider credentials. One deployment is one workspace; the id is
    # stored with every key so a shared database cannot mix two workspaces.
    workspace_id: str = Field(default="default", min_length=1, max_length=64)
    # ``auto`` prefers age when a recipient is configured, then the OS keyring. There is
    # no plaintext fallback: an unavailable store refuses creation.
    provider_key_store: Literal["auto", "age", "keyring"] = "auto"
    provider_key_age_recipient: str | None = None
    provider_key_age_identity: str | None = None
    provider_key_age_binary: str = "age"
    provider_key_fingerprint_pepper: SecretStr | None = None
    # A peer in this list may supply X-Forwarded-For for the loopback test on
    # operational endpoints, in addition to a loopback peer. Empty by default. List the
    # reverse proxy's own address, never a client network.
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)

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

    # Bound the market discovery fan-out so one slow or failing provider cannot hold
    # a scan or a request open. Safe defaults; validated below.
    market_discovery_deadline_seconds: float = Field(default=10.0, ge=0.1, le=120)
    market_provider_failure_threshold: int = Field(default=3, ge=1, le=20)
    market_provider_cooldown_seconds: float = Field(default=60.0, ge=1, le=3600)
    # Aliases kept for callers that use the shorter names. ``None`` means "use the
    # canonical setting", so an alias supplied on its own still takes effect.
    market_discovery_timeout_seconds: float | None = Field(default=None, ge=0.1, le=120)
    market_provider_max_failures: int | None = Field(default=None, ge=1, le=20)
    market_circuit_breaker_cooldown_seconds: float | None = Field(default=None, ge=1, le=3600)

    paper_starting_cash_usd: Decimal = Field(default=Decimal("10000"))
    paper_fee_bps: int = 30
    # Paper-only no-risk mode: a buy that would overdraw cash is funded for the
    # exact shortfall through a labelled funding journal group instead of being
    # rejected. Never affects a real-risk gate and never leaves paper.
    paper_no_risk: bool = False
    max_order_usd: Decimal = Field(default=Decimal("1000"))
    max_position_usd: Decimal = Field(default=Decimal("2500"))
    max_open_positions: int = 10
    max_daily_loss_usd: Decimal = Field(default=Decimal("500"))
    max_quote_age_seconds: int = 60
    min_pair_age_minutes: int = 10
    min_liquidity_usd: Decimal = Field(default=Decimal("10000"))
    paper_mark_max_order_usd: Decimal = Field(default=Decimal("250"))
    # Unauthenticated read rate limit. Zero disables it (development only).
    read_rate_limit_per_minute: int = Field(default=60, ge=0, le=100000)
    # Caps on a single quote request. Exceeded before any provider call.
    max_quote_notional_usd: Decimal = Field(default=Decimal("1000"), gt=0)
    max_quote_slippage_bps: int = Field(default=500, ge=0, le=10000)
    # Execution-cost ceiling for a new position, as a ratio (0.05 = 5%). Applies to
    # executable quotes only; a paper_mark reports no impact and a sell is never blocked.
    max_price_impact_pct: Decimal = Field(default=Decimal("0.05"), ge=0, le=1)
    # Server-side trade risk profile. A client may request this profile by name only
    # when the server allows it for the account; the active profile is configuration.
    trade_risk_profile: Literal["low", "mid", "high"] | None = Field(
        default=None,
        validation_alias=AliasChoices("trade_risk_profile", "risk_profile", "trade_profile"),
    )
    # Paper-to-live promotion gate (ADR-0009). Computed, never auto-promoted.
    paper_gate_min_days: int = Field(default=14, ge=1, le=365)
    paper_gate_min_intents: int = Field(default=100, ge=1)
    paper_gate_min_fills: int = Field(default=20, ge=1)
    paper_gate_require_backtest: bool = True

    cors_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))

    @field_validator(
        "api_token", "api_key_pepper", "provider_key_fingerprint_pepper", mode="before"
    )
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        """An empty env var means "unset", matching the documented dev fallback."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("trade_risk_profile")
    @classmethod
    def _valid_trade_risk_profile(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in {"low", "mid", "high"}:
            raise ValueError("EZCHAIN_TRADE_RISK_PROFILE must be one of low, mid, high")
        return value

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _valid_trusted_proxies(cls, values: list[str]) -> list[str]:
        """Refuse a typo and a list that trusts the whole internet."""
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                continue
            try:
                network = ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"EZCHAIN_TRUSTED_PROXY_CIDRS contains an invalid network: {item!r}"
                ) from exc
            if network.prefixlen == 0:
                raise ValueError(
                    "EZCHAIN_TRUSTED_PROXY_CIDRS must not trust every address; "
                    "list the proxy's own network"
                )
            cleaned.append(item)
        return cleaned

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
        if self.read_rate_limit_per_minute == 0:
            raise ValueError("EZCHAIN_READ_RATE_LIMIT_PER_MINUTE must be greater than zero in prod")
        if self.paper_no_risk:
            raise ValueError("EZCHAIN_PAPER_NO_RISK must be false in prod")
        return self

    @property
    def plugin_path_list(self) -> list[str]:
        return [item.strip() for item in self.plugin_paths.split(",") if item.strip()]

    @property
    def risk_profile(self) -> str | None:
        return self.trade_risk_profile

    @property
    def trade_profile(self) -> str | None:
        return self.trade_risk_profile


@lru_cache
def get_settings() -> Settings:
    return Settings()
