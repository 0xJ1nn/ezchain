"""Runtime settings. One home for every number."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EZCHAIN_", env_file=".env", extra="ignore")

    app_name: str = "ezchain-api"
    environment: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://jinultimate@localhost:5432/ezchain"
    auto_create_schema: bool = True

    data_mode: Literal["live", "fixture"] = "live"
    engine_inline: bool = True
    engine_interval_seconds: int = 30
    # Seed for the persisted operator kill switch when ``system_flags`` has no row yet.
    engine_kill_switch: bool = False

    paper_starting_cash_usd: Decimal = Field(default=Decimal("10000"))
    paper_fee_bps: int = 30
    paper_mark_fee_bps: int = 30
    max_order_usd: Decimal = Field(default=Decimal("1000"))
    max_position_usd: Decimal = Field(default=Decimal("2500"))
    max_open_positions: int = 10
    max_daily_loss_usd: Decimal = Field(default=Decimal("500"))
    max_quote_age_seconds: int = 60
    min_liquidity_usd: Decimal = Field(default=Decimal("10000"))
    paper_mark_max_order_usd: Decimal = Field(default=Decimal("250"))

    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
