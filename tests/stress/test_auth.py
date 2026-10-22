"""Bearer-token handling for an auth-enabled paper API."""

from __future__ import annotations

import pytest
from tools.stress.config import ConfigError, RunConfig
from tools.stress.http import Harness


def test_token_is_sent_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    config = RunConfig(base_url="http://127.0.0.1:8000")
    monkeypatch.delenv("EZCHAIN_STRESS_API_TOKEN", raising=False)
    without = Harness(config)
    try:
        assert "authorization" not in without._client(config.origin).headers
    finally:
        without.close_thread_clients()

    monkeypatch.setenv("EZCHAIN_STRESS_API_TOKEN", "secret-token")
    with_token = Harness(config)
    try:
        assert with_token._client(config.origin).headers["authorization"] == "Bearer secret-token"
        assert "secret-token" not in with_token.redact("boom secret-token boom")
    finally:
        with_token.close_thread_clients()


def test_token_over_plain_http_to_a_remote_host_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZCHAIN_STRESS_API_TOKEN", "secret-token")
    with pytest.raises(ConfigError):
        Harness(RunConfig(base_url="http://example.com:8000"))


def test_token_over_https_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZCHAIN_STRESS_API_TOKEN", "secret-token")
    harness = Harness(RunConfig(base_url="https://example.com"))
    try:
        assert harness._client("https://example.com").headers["authorization"].startswith("Bearer ")
    finally:
        harness.close_thread_clients()
