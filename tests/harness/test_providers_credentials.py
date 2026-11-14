"""Criterion (b): the credential never reaches a log, a record or a rendered page.

The key is read from one environment variable and exists only long enough to build one request
header. These tests set a fake key with a real shape, exercise the client — including a path
where the provider echoes the key back inside an error body — and then search every artefact a
caller could write down: emitted log lines, the session record file, the record's JSON and a
rendered HTML page built from it.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest
from providers_helpers import (
    KEY,
    PINNED_MODEL,
    PRICING,
    ScriptedTransport,
    anthropic_text,
    anthropic_tool_use,
    record,
)

from ezchain_agent_runtime import Message, ModelRequest
from ezchain_harness import (
    AnthropicClient,
    Credential,
    HttpResponse,
    MissingCredential,
    OpenAICompatibleClient,
    ProviderClient,
    ProviderHTTPError,
    redact,
)

REQUEST = ModelRequest(messages=(Message(role="user", content="hi"),), tools=())


def _anthropic(
    transport: ScriptedTransport, logger: logging.Logger | None = None
) -> AnthropicClient:
    return AnthropicClient(
        model_id=PINNED_MODEL,
        pricing=PRICING,
        record=record(),
        transport=transport,
        logger=logger,
    )


def test_no_client_accepts_a_key_as_a_constructor_argument() -> None:
    forbidden = ("key", "api_key", "token", "secret", "password", "credential")
    for cls in (ProviderClient, AnthropicClient, OpenAICompatibleClient):
        parameters = inspect.signature(cls.__init__).parameters
        for name in parameters:
            if name == "self":
                continue
            lowered = name.lower()
            assert not any(word in lowered for word in forbidden), (
                f"{cls.__name__} takes {name!r}; a credential must never be an argument"
            )


def test_the_client_takes_its_key_from_the_environment_only() -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "a"}')])

    _anthropic(transport).complete(REQUEST)

    assert transport.requests[0].headers["x-api-key"] == KEY


def test_an_absent_key_fails_before_any_request_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    transport = ScriptedTransport([anthropic_text('{"summary": "a"}')])

    try:
        _anthropic(transport).complete(REQUEST)
    except MissingCredential as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
        assert KEY not in str(exc)
    else:
        raise AssertionError("a missing key must fail closed")

    assert transport.calls == 0


def test_a_credential_renders_as_redacted() -> None:
    credential = Credential.from_env("ANTHROPIC_API_KEY")

    assert KEY not in str(credential)
    assert KEY not in repr(credential)
    assert KEY not in f"{credential}"
    assert "[redacted]" in str(credential)


def test_no_log_line_carries_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    transport = ScriptedTransport(
        [anthropic_tool_use(), anthropic_text('{"summary": "The order is in."}')]
    )
    client = _anthropic(transport)

    client.complete(REQUEST)
    client.complete(REQUEST)

    assert caplog.records, "the client should log one line per turn"
    for record_ in caplog.records:
        assert KEY not in record_.getMessage()
        assert KEY not in str(record_.args)
        assert KEY not in str(record_.exc_info)
    assert any("model=" in item.getMessage() for item in caplog.records)


def test_a_provider_error_that_echoes_the_key_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    transport = ScriptedTransport(
        [
            HttpResponse(
                status=401,
                body={
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": f"invalid x-api-key: {KEY}",
                    },
                },
            )
        ]
    )

    try:
        _anthropic(transport).complete(REQUEST)
    except ProviderHTTPError as exc:
        assert KEY not in str(exc)
        assert "[redacted]" in str(exc)
    else:
        raise AssertionError("a 401 must raise")

    for record_ in caplog.records:
        assert KEY not in record_.getMessage()


def test_the_record_file_and_its_rendered_json_carry_no_key(tmp_path: Path) -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "a"}')])
    client = _anthropic(transport)

    client.complete(REQUEST)
    path = client.record.write(tmp_path / "session.json", secrets=(KEY,))

    written = path.read_text()
    assert KEY not in written
    assert PINNED_MODEL in written
    assert KEY not in client.record.render(secrets=(KEY,))


def test_a_rendered_page_built_from_the_record_carries_no_key(tmp_path: Path) -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "a"}')])
    client = _anthropic(transport)

    client.complete(REQUEST)
    payload = client.record.render(secrets=(KEY,))
    page = (
        "<!doctype html><html><body><pre>" + payload.replace("<", "&lt;") + "</pre></body></html>"
    )
    target = tmp_path / "session.html"
    target.write_text(page, encoding="utf-8")

    html = target.read_text()
    assert KEY not in html
    assert "TOPSECRETVALUE" not in html
    assert PINNED_MODEL in html


def test_the_redactor_scrubs_a_key_of_an_unusual_shape() -> None:
    odd = "totally-unremarkable-string-1234"

    assert odd not in redact(f"header says {odd}", secrets=(odd,))
    assert redact(f"header says {odd}", secrets=(odd,)) == "header says [redacted]"
