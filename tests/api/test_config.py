"""Production config refusals and the permissive-but-warned development default."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ezchain_api.config import DEFAULT_DATABASE_URL, Settings

PROD = {
    "environment": "prod",
    "api_token": "a" * 40,
    "auth_enabled": True,
    "auto_create_schema": False,
    "database_url": "postgresql+psycopg://ezchain:long-password@db:5432/ezchain",
    "data_mode": "live",
    "cors_origins": ["https://app.example.com"],
}


def prod_settings(**overrides: object) -> Settings:
    values = dict(PROD)
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_a_safe_production_boot_is_accepted() -> None:
    settings = prod_settings()

    assert settings.environment == "prod"
    assert settings.api_token is not None
    assert "a" * 40 not in repr(settings)
    assert "SecretStr('**********')" in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"auth_enabled": False}, "EZCHAIN_AUTH_ENABLED"),
        ({"api_token": None}, "EZCHAIN_API_TOKEN"),
        ({"api_token": "too-short"}, "EZCHAIN_API_TOKEN"),
        ({"api_token": "\u00e9" * 40}, "EZCHAIN_API_TOKEN"),
        ({"auto_create_schema": True}, "EZCHAIN_AUTO_CREATE_SCHEMA"),
        ({"database_url": DEFAULT_DATABASE_URL}, "EZCHAIN_DATABASE_URL"),
        (
            {"database_url": "postgresql+psycopg://ezchain@db:5432/ezchain"},
            "EZCHAIN_DATABASE_URL",
        ),
        ({"data_mode": "fixture"}, "EZCHAIN_DATA_MODE"),
        ({"cors_origins": ["*"]}, "EZCHAIN_CORS_ORIGINS"),
    ],
)
def test_unsafe_production_config_names_the_exact_key(
    overrides: dict[str, object], key: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        prod_settings(**overrides)

    assert key in str(raised.value)


def test_development_defaults_keep_working() -> None:
    settings = Settings(environment="dev")

    assert settings.auth_enabled is True
    assert settings.api_token is None
    assert settings.auto_create_schema is True
    assert settings.data_mode == "live"


def test_live_mode_is_refused_until_the_executor_exists() -> None:
    with pytest.raises(ValidationError, match="EZCHAIN_EXECUTION_MODE"):
        Settings(environment="dev", execution_mode="live")  # type: ignore[arg-type]


def test_blank_secrets_are_unset() -> None:
    settings = Settings(environment="dev", api_token="", api_key_pepper="  ")
    assert settings.api_token is None
    assert settings.api_key_pepper is None


def test_plugin_paths_are_comma_separated() -> None:
    settings = Settings(environment="dev", plugin_paths="/srv/a, /srv/b ,")
    assert settings.plugin_path_list == ["/srv/a", "/srv/b"]


def test_test_environment_may_use_sqlite_and_fixtures() -> None:
    settings = Settings(environment="test", data_mode="fixture", database_url="sqlite+pysqlite://")

    assert settings.environment == "test"
    assert settings.data_mode == "fixture"
