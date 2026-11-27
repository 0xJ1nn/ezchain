"""Model providers at the API edge: labelled tiers, key safety, reported usage.

A stored key is encrypted before it reaches the row, is used only as an outbound header and
never appears in a response, a log line or a recorded chat. A local model opens a chat with
no key in the workspace at all.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi.testclient import TestClient
from tests.harness.providers_helpers import ScriptedTransport, openai_text

from ezchain_api.models import ProviderKeyRow
from ezchain_api.routers import agent as agent_router
from ezchain_api.routers import provider_keys as provider_keys_router
from ezchain_api.services import agent_models, provider_keys
from ezchain_harness import HttpResponse

TEST_ONLY_KEY = "TEST_ONLY_KEY_model_provider_do_not_use"


def _store_key(session: Any, cipher: Any, provider: str) -> None:
    provider_keys.create_key(
        session,
        workspace_id="default",
        provider=provider,
        secret=TEST_ONLY_KEY,
        cipher=cipher,
    )


def test_model_list_labels_each_model_and_never_returns_a_key(
    client_for: Any, session: Any, cipher: Any
) -> None:
    _store_key(session, cipher, "openrouter")
    client: TestClient = client_for(agent_router.router)

    response = client.get("/api/v1/agent/models")

    assert response.status_code == 200
    body = response.json()
    by_id = {item["id"]: item for item in body["models"]}
    assert by_id["ollama/llama3.2"]["tier"] == "free"
    assert by_id["openrouter/llama-3.3-70b"]["tier"] == "your_key"
    assert by_id["openrouter/llama-3.3-70b"]["has_key"] is True
    assert by_id["anthropic/claude-sonnet-4-5"]["has_key"] is False
    assert by_id["gateway/general"]["tier"] == "paid"
    assert TEST_ONLY_KEY not in response.text
    assert "secret" not in json.dumps(body).lower()


def test_storing_a_model_provider_key_keeps_it_encrypted_and_out_of_the_api(
    client_for: Any, session: Any, cipher: Any
) -> None:
    client: TestClient = client_for(provider_keys_router.router)

    created = client.post(
        "/api/v1/provider-keys",
        json={"provider": "openrouter", "label": "desk", "key": TEST_ONLY_KEY},
    )

    assert created.status_code == 201
    assert TEST_ONLY_KEY not in created.text
    row = session.query(ProviderKeyRow).one()
    assert TEST_ONLY_KEY not in row.secret_ciphertext
    assert row.fingerprint
    listing = client.get("/api/v1/provider-keys")
    assert TEST_ONLY_KEY not in listing.text
    assert "secret_ciphertext" not in listing.text


def test_a_local_chat_needs_no_key_and_reports_zero_cost(client_for: Any) -> None:
    transport = ScriptedTransport(
        [openai_text('{"summary": "done"}', prompt_tokens=7, completion_tokens=3)]
    )
    client: TestClient = client_for(
        agent_router.router,
        {agent_models.transport_for_tests: lambda: transport},
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"provider": "ollama", "model_id": "ollama/llama3.2", "prompt": "hello"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["usage"]["prompt_tokens"] == 7
    assert body["usage"]["completion_tokens"] == 3
    assert body["usage"]["total_tokens"] == 10
    assert body["usage"]["cost_usd"] == "0.000000"
    assert "authorization" not in transport.requests[0].headers


def test_a_keyed_chat_uses_the_stored_secret_and_reports_usage(
    client_for: Any, session: Any, cipher: Any, caplog: Any
) -> None:
    _store_key(session, cipher, "openrouter")
    transport = ScriptedTransport([openai_text("answer", prompt_tokens=100, completion_tokens=50)])
    caplog.set_level(logging.DEBUG)
    client: TestClient = client_for(
        agent_router.router,
        {agent_models.transport_for_tests: lambda: transport},
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "provider": "openrouter",
            "model_id": "openrouter/llama-3.3-70b",
            "prompt": "hello",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["prompt_tokens"] == 100
    assert body["usage"]["completion_tokens"] == 50
    assert body["usage"]["reported"] is True
    assert float(body["usage"]["cost_usd"]) > 0
    assert transport.requests[0].headers["authorization"] == f"Bearer {TEST_ONLY_KEY}"
    assert TEST_ONLY_KEY not in response.text
    for record_ in caplog.records:
        assert TEST_ONLY_KEY not in record_.getMessage()


def test_a_chat_without_a_required_key_is_refused_before_a_call(client_for: Any) -> None:
    transport = ScriptedTransport([openai_text("answer")])
    client: TestClient = client_for(
        agent_router.router,
        {agent_models.transport_for_tests: lambda: transport},
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "provider": "anthropic",
            "model_id": "anthropic/claude-sonnet-4-5",
            "prompt": "hello",
        },
    )

    assert response.status_code == 422
    assert transport.calls == 0


def test_a_paid_model_without_a_gateway_is_refused(client_for: Any) -> None:
    client: TestClient = client_for(agent_router.router)

    response = client.post(
        "/api/v1/agent/chat",
        json={"provider": "gateway", "model_id": "gateway/general", "prompt": "hello"},
    )

    assert response.status_code == 422
    assert "gateway" in response.text.lower()


def test_a_turn_that_reports_no_usage_shows_zero_and_not_reported(client_for: Any) -> None:
    transport = ScriptedTransport(
        [
            HttpResponse(
                status=200,
                body={
                    "choices": [{"message": {"role": "assistant", "content": "answer"}}],
                },
            )
        ]
    )
    client: TestClient = client_for(
        agent_router.router,
        {agent_models.transport_for_tests: lambda: transport},
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"provider": "ollama", "model_id": "ollama/llama3.2", "prompt": "hello"},
    )

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["prompt_tokens"] == 0
    assert usage["reported"] is False


def test_an_unknown_model_is_refused(client_for: Any) -> None:
    client: TestClient = client_for(agent_router.router)

    response = client.post(
        "/api/v1/agent/chat",
        json={"provider": "ollama", "model_id": "nope/nope", "prompt": "hello"},
    )

    assert response.status_code == 422
