"""The provider registry: one client per wire protocol, providers as configuration.

The six providers this field names reduce to two wire protocols. These tests drive the real
clients through a scripted transport: a new provider is a registry entry, not a new client,
and a local entry opens a session with no credential set anywhere.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator

import pytest

from ezchain_agent_runtime import Message, ModelRequest
from ezchain_harness import (
    AnthropicClient,
    Credential,
    MissingCredential,
    OpenAICompatibleClient,
    ProviderClient,
    Trace,
    TracedModel,
)
from ezchain_harness.factory import build_model
from ezchain_harness.registry import (
    MODELS,
    WIRE_CLIENTS,
    ModelSpec,
    ProviderSpec,
    UnknownModel,
    UnknownProvider,
    get_model,
    get_provider,
    list_models,
    list_providers,
    resolve_base_url,
)
from ezchain_harness.secrets import provide_secret

from .providers_helpers import ScriptedTransport, openai_text

REQUEST = ModelRequest(messages=(Message(role="user", content="hello"),), tools=())


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "COMMAND_CODE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def test_there_is_one_client_per_wire_protocol() -> None:
    assert WIRE_CLIENTS == {
        "anthropic": AnthropicClient,
        "openai": OpenAICompatibleClient,
    }
    assert issubclass(WIRE_CLIENTS["anthropic"], ProviderClient)
    assert issubclass(WIRE_CLIENTS["openai"], ProviderClient)


def test_every_provider_names_a_wire_protocol_and_builds_its_client() -> None:
    providers = list_providers()
    assert {spec.name for spec in providers} == {
        "anthropic",
        "openai",
        "openrouter",
        "command_code",
        "opencode",
        "ollama",
        "lm_studio",
        "openai_compatible",
        "gateway",
    }
    for spec in providers:
        assert isinstance(spec, ProviderSpec)
        assert spec.wire in WIRE_CLIENTS


def test_adding_a_provider_is_a_configuration_entry_not_a_client() -> None:
    added = ProviderSpec(
        name="new_gateway",
        wire="openai",
        label="A new gateway",
        base_url="http://localhost:9999/v1",
        env_name=None,
        auth_optional=True,
    )
    spec = get_provider("new_gateway", registry=(added,))
    client = build_model(
        "new_gateway/anything",
        session_id="s-new",
        registry=(added,),
        model_specs=(
            ModelSpec(
                id="new_gateway/anything",
                provider="new_gateway",
                label="Anything",
                tier="free",
                pricing=MODELS[0].pricing,
            ),
        ),
        transport=ScriptedTransport([]),
    )
    assert spec is added
    assert isinstance(client, OpenAICompatibleClient)


def test_wire_and_base_url_come_from_the_provider_entry() -> None:
    anthropic = get_provider("anthropic")
    openrouter = get_provider("openrouter")
    assert anthropic.wire == "anthropic"
    assert openrouter.wire == "openai"
    assert resolve_base_url(anthropic) == "https://api.anthropic.com"
    assert resolve_base_url(openrouter) == "https://openrouter.ai/api/v1"
    assert openrouter.env_name == "OPENROUTER_API_KEY"


def test_a_base_url_can_be_overridden_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZCHAIN_OPENCODE_BASE_URL", "http://127.0.0.1:4010/v1")
    spec = get_provider("opencode")
    assert resolve_base_url(spec) == "http://127.0.0.1:4010/v1"


def test_the_model_list_says_free_your_key_or_paid() -> None:
    tiers = {model.tier for model in list_models()}
    assert tiers == {"free", "your_key", "paid"}
    assert get_model("ollama/llama3.2").tier == "free"
    assert get_model("anthropic/claude-sonnet-4-5").tier == "your_key"
    assert get_model("gateway/general").tier == "paid"
    assert get_model("gateway/general").available is False


def test_a_local_model_needs_no_key_and_sends_no_credential() -> None:
    transport = ScriptedTransport([openai_text('{"summary": "done"}')])
    client = build_model(
        "ollama/llama3.2",
        session_id="s-local",
        transport=transport,
    )

    turn = client.complete(REQUEST)

    assert isinstance(client, OpenAICompatibleClient)
    assert turn.text == '{"summary": "done"}'
    assert "authorization" not in transport.requests[0].headers
    assert "x-api-key" not in transport.requests[0].headers
    assert transport.requests[0].url == "http://localhost:11434/v1/chat/completions"
    assert turn.usage.cost_usd == 0


def test_lm_studio_works_with_no_key_on_its_own_endpoint() -> None:
    transport = ScriptedTransport([openai_text('{"summary": "done"}')])
    client = build_model("lm_studio/local-model", session_id="s-lm", transport=transport)

    client.complete(REQUEST)

    assert transport.requests[0].url == "http://localhost:1234/v1/chat/completions"
    assert "authorization" not in transport.requests[0].headers


def test_an_anthropic_model_still_fails_closed_without_its_key() -> None:
    transport = ScriptedTransport([openai_text('{"summary": "done"}')])
    client = build_model(
        "anthropic/claude-sonnet-4-5",
        session_id="s-keyed",
        transport=transport,
    )

    with pytest.raises(MissingCredential) as caught:
        client.complete(REQUEST)

    assert "ANTHROPIC_API_KEY" in str(caught.value)
    assert transport.calls == 0


def test_a_stored_key_is_used_from_a_scoped_context_and_never_serialized() -> None:
    secret = "TEST_ONLY_KEY_model_provider"
    transport = ScriptedTransport([openai_text('{"summary": "done"}')])
    client = build_model(
        "openrouter/llama-3.3-70b",
        session_id="s-keyed",
        transport=transport,
    )

    with provide_secret("OPENROUTER_API_KEY", secret):
        credential = Credential.from_env("OPENROUTER_API_KEY")
        assert credential.secret() == secret
        assert secret not in repr(credential)
        assert secret not in str(credential)
        client.complete(REQUEST)

    assert transport.requests[0].headers["authorization"] == f"Bearer {secret}"
    assert "OPENROUTER_API_KEY" not in os.environ


def test_a_secret_from_context_is_gone_after_the_block() -> None:
    secret = "TEST_ONLY_KEY_scoped"
    with pytest.raises(RuntimeError):
        with provide_secret("OPENROUTER_API_KEY", secret):
            raise RuntimeError("leave the block")
    with pytest.raises(MissingCredential):
        Credential.from_env("OPENROUTER_API_KEY")


def test_a_scoped_key_never_reaches_the_trace() -> None:
    secret = "TEST_ONLY_KEY_trace"
    transport = ScriptedTransport([openai_text("answer")])
    client = build_model("openrouter/llama-3.3-70b", session_id="s-trace", transport=transport)
    stream = io.StringIO()
    traced = TracedModel(client, Trace(stream))

    with provide_secret("OPENROUTER_API_KEY", secret):
        traced.complete(REQUEST)

    output = stream.getvalue()
    assert "turn" in output
    assert secret not in output
    rendered = client.record.render(secrets=(secret,))
    assert secret not in rendered


def test_each_turn_records_tokens_and_price_from_the_registry_entry() -> None:
    transport = ScriptedTransport(
        [openai_text('{"summary": "done"}', prompt_tokens=100, completion_tokens=50)]
    )
    client = build_model("openai/gpt-4o-mini", session_id="s-turn", transport=transport)

    with provide_secret("OPENAI_API_KEY", "TEST_ONLY_KEY_turn"):
        turn = client.complete(REQUEST)

    assert turn.usage.prompt_tokens == 100
    assert turn.usage.completion_tokens == 50
    assert turn.usage.cost_usd > 0
    assert len(client.record.turns) == 1
    assert client.record.turns[0].model_id == "openai/gpt-4o-mini"


def test_an_unknown_model_or_provider_is_refused() -> None:
    with pytest.raises(UnknownModel):
        get_model("not/provider")
    with pytest.raises(UnknownProvider):
        get_provider("not-a-provider")
