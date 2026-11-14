"""The real transport: one call, mapped failures, no retry.

``HttpxTransport`` is the only thing in the package that touches the network, so these tests
replace ``httpx.request`` itself and assert on what the transport did with the answer — a
timeout and a connection reset must arrive as this package's typed errors, and neither may
cause a second attempt.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from ezchain_harness import (
    HttpRequest,
    HttpxTransport,
    ProviderTimeout,
    ProviderTransportError,
)

REQUEST = HttpRequest(
    method="POST",
    url="https://provider.test/v1/messages",
    headers={"x-api-key": "sk-not-a-real-key-value"},
    json={"model": "m"},
    timeout=7.5,
)


def _patch(monkeypatch: pytest.MonkeyPatch, handler: Callable[..., object]) -> list[int]:
    calls: list[int] = []

    def spy(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return handler(*args, **kwargs)

    monkeypatch.setattr(httpx, "request", spy)
    return calls


def test_a_timeout_is_a_provider_timeout_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args: object, **kwargs: object) -> object:
        raise httpx.ReadTimeout("too slow")

    calls = _patch(monkeypatch, explode)

    with pytest.raises(ProviderTimeout) as caught:
        HttpxTransport()(REQUEST)

    assert "7.5s" in str(caught.value)
    assert calls == [1]


def test_a_connection_failure_is_a_transport_error_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args: object, **kwargs: object) -> object:
        raise httpx.ConnectError("connection reset by peer")

    calls = _patch(monkeypatch, explode)

    with pytest.raises(ProviderTransportError) as caught:
        HttpxTransport()(REQUEST)

    assert "ConnectError" in str(caught.value)
    assert calls == [1]


def test_a_response_is_returned_with_its_status_and_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(*args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"ok": True},
            request=httpx.Request("POST", "https://provider.test/v1/messages"),
        )

    _patch(monkeypatch, answer)

    response = HttpxTransport()(REQUEST)

    assert response.status == 200
    assert response.body == {"ok": True}


def test_a_non_json_body_is_an_empty_body_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(*args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            status_code=502,
            text="<html>bad gateway</html>",
            request=httpx.Request("POST", "https://provider.test/v1/messages"),
        )

    _patch(monkeypatch, answer)

    response = HttpxTransport()(REQUEST)

    assert response.status == 502
    assert response.body == {}
