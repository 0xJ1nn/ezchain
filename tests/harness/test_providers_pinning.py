"""Criterion (a): the model id is pinned per session and recorded.

A session's model is chosen once and handed to the client as a value. These tests change the
environment *after* the client exists and prove the request and the record still carry the
id the session was built with — the failure the pin prevents is a session that reports one
model and bills another.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

from providers_helpers import (
    OPENAI_MODEL,
    PINNED_MODEL,
    PRICING,
    ScriptedTransport,
    anthropic_text,
    openai_text,
    record,
)

from ezchain_agent_runtime import Message, ModelRequest
from ezchain_harness import (
    AnthropicClient,
    OpenAICompatibleClient,
    SessionRecord,
)
from ezchain_harness.factory import MODEL_ENV, build, model_from_env

REQUEST = ModelRequest(messages=(Message(role="user", content="hello"),), tools=())


def test_changing_the_model_env_mid_session_does_not_change_what_is_sent() -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "a"}')])
    client = AnthropicClient(
        model_id=PINNED_MODEL,
        pricing=PRICING,
        record=record(),
        transport=transport,
    )
    try:
        client.complete(REQUEST)
        # The operator (or a stray dotenv) changes the environment after the session opened.
        os.environ[MODEL_ENV] = "some-other-model"
        transport._script.append(anthropic_text('{"summary": "b"}'))
        client.complete(REQUEST)

        assert [sent.json["model"] for sent in transport.requests] == [PINNED_MODEL, PINNED_MODEL]
        assert {turn.model_id for turn in client.record.turns} == {PINNED_MODEL}
        assert client.model_id == PINNED_MODEL
    finally:
        os.environ.pop(MODEL_ENV, None)


def test_the_pinned_id_lands_in_the_recorded_session() -> None:
    transport = ScriptedTransport([openai_text('{"summary": "a"}')])
    session = record(provider="openai", model_id=OPENAI_MODEL)
    client = OpenAICompatibleClient(
        model_id=OPENAI_MODEL,
        pricing=PRICING,
        record=session,
        transport=transport,
    )

    client.complete(REQUEST)

    rendered = json.loads(session.render())
    assert rendered["model_id"] == OPENAI_MODEL
    assert rendered["provider"] == "openai"
    assert [turn["model_id"] for turn in rendered["turns"]] == [OPENAI_MODEL]


def test_a_record_pinned_to_another_model_is_refused_at_construction() -> None:
    session = SessionRecord(session_id="s1", provider="anthropic", model_id="claude-opus-4-1")
    try:
        AnthropicClient(
            model_id=PINNED_MODEL,
            pricing=PRICING,
            record=session,
            transport=ScriptedTransport([]),
        )
    except ValueError as exc:
        assert "pinned" in str(exc)
    else:
        raise AssertionError("a client must not run under a record pinned to another model")


def test_a_turn_under_another_model_id_is_refused_by_the_record() -> None:
    session = record()

    session.add_turn(
        model_id=PINNED_MODEL,
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=Decimal("0"),
        tool_calls=0,
        stop="answer",
        latency_ms=1,
    )
    try:
        session.add_turn(
            model_id="some-other-model",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=Decimal("0"),
            tool_calls=0,
            stop="answer",
            latency_ms=1,
        )
    except ValueError as exc:
        assert "pinned" in str(exc)
    else:
        raise AssertionError("the record must reject a turn under a different model")


def test_build_pins_the_id_it_was_given_and_the_env_only_names_a_default() -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "a"}')])
    client = build(
        provider="anthropic",
        model_id=PINNED_MODEL,
        pricing=PRICING,
        session_id="s-pinned",
        transport=transport,
    )

    client.complete(REQUEST)

    assert transport.requests[0].json["model"] == PINNED_MODEL
    assert client.record.session_id == "s-pinned"
    assert client.record.model_id == PINNED_MODEL
    assert model_from_env({MODEL_ENV: "  "}) == ""
    assert model_from_env({MODEL_ENV: "claude-from-env"}) == "claude-from-env"
