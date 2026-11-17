"""Model providers are configuration: one client per wire protocol, no code per provider."""

from __future__ import annotations

import pytest

from ezchain_harness import providers
from ezchain_harness.factory import build
from ezchain_harness.providers import ProviderSpec, get, model_catalogue, names, register
from ezchain_harness.transport import HttpResponse
from tests.harness.providers_helpers import PRICING, ScriptedTransport


def test_every_required_provider_is_registered() -> None:
    required = {
        "anthropic",
        "opencode_zen",
        "opencode_go",
        "openrouter",
        "command_code",
        "openai_compatible",
        "ollama",
        "lm_studio",
    }
    assert required <= set(names())


def test_each_provider_declares_its_wire_protocol_and_billing() -> None:
    for name in names():
        spec = get(name)
        assert spec.protocol in ("anthropic", "openai")
        assert spec.billing in ("free", "your_key", "paid")
        assert spec.title
        if spec.billing == "free" and name not in ("opencode_go", "ollama", "lm_studio"):
            assert spec.api_key_env is not None


def test_local_endpoints_need_no_key() -> None:
    for name in ("ollama", "lm_studio"):
        spec = get(name)
        assert spec.api_key_env is None
        assert spec.default_base_url
        assert spec.billing == "free"


def test_model_labels_distinguish_free_your_key_and_paid() -> None:
    catalogue = {item["name"]: item for item in model_catalogue()}
    assert catalogue["ollama"]["label"] == "free"
    assert catalogue["anthropic"]["label"] == "your key"
    assert catalogue["command_code"]["label"] == "paid"


def test_adding_a_provider_is_a_config_call_not_a_code_change() -> None:
    spec = ProviderSpec(
        name="test_provider",
        title="Test Provider",
        protocol="openai",
        billing="your_key",
        api_key_env="TEST_PROVIDER_KEY",
        default_base_url="https://provider.test/v1",
    )
    register(spec)
    try:
        assert get("test_provider") == spec
        assert "test_provider" in names()
    finally:
        providers.unregister("test_provider")
    with pytest.raises(ValueError):
        get("test_provider")


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ValueError):
        get("not_registered")


def test_the_factory_builds_a_client_for_a_registered_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCODE_KEY", "test-key")
    transport = ScriptedTransport([])
    client = build(
        provider="opencode_go",
        model_id="test-model",
        pricing=PRICING,
        session_id="session-1",
        transport=transport,
        base_url="https://opencode.test/v1",
    )
    assert client.model_id == "test-model"
    assert client.record.provider == "opencode_go"


def test_a_local_client_sends_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    body = {
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    transport = ScriptedTransport([HttpResponse(status=200, body=body)])
    client = build(
        provider="ollama",
        model_id="local-model",
        pricing=PRICING,
        session_id="session-2",
        transport=transport,
    )
    from ezchain_agent_runtime import Message, ModelRequest

    client.complete(ModelRequest(messages=(Message(role="user", content="hi"),), tools=()))
    header = dict(transport.requests[0].headers)
    assert "authorization" not in {key.lower() for key in header}
