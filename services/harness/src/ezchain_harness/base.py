"""The shared half of a provider client: pinning, credentials, spend and the record.

A concrete client supplies four wire details — the endpoint, the headers, the request body
and the response decoder — and everything a session must be able to prove lives here:

**Pinned.** ``model_id`` is a constructor argument and the only id any request carries.
Nothing reads the model from the environment after construction, so changing the environment
mid-session cannot change what a running session sends or what it records.

**Credential.** There is no key argument. :class:`~.credentials.Credential` reads the
environment on every call, so a rotated key is picked up without rebuilding the client, and
the value exists only long enough to build one request header. Every line this class logs is
redacted with the live value.

**Charged.** Each answered turn is priced with the operator's :class:`~.pricing.ModelPricing`
and written to the session's :class:`~.record.SessionRecord` under the pinned id.

**No retry.** :meth:`complete` makes exactly one transport call. A failure raises; it never
loops, backs off or replays. The loop turns that raise into a failed session, which is what
keeps a turn whose tool calls already ran from running them a second time.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

from ezchain_agent_runtime import ModelRequest, ModelTurn, ModelUsage

from .credentials import Credential
from .errors import ProviderHTTPError, ProviderProtocolError
from .pricing import ModelPricing
from .record import SessionRecord
from .redact import redact
from .transport import HttpRequest, HttpResponse, HttpxTransport, Transport

DEFAULT_TIMEOUT_SECONDS = 60.0

LOGGER_NAME = "ezchain_harness.providers"


def utcnow() -> datetime:
    return datetime.now(UTC)


def usage_from(raw: Any) -> tuple[int, int]:
    """``(prompt_tokens, completion_tokens)`` from a provider usage object, or zeros."""
    if not isinstance(raw, Mapping):
        return 0, 0
    prompt = raw.get("input_tokens", raw.get("prompt_tokens", 0))
    completion = raw.get("output_tokens", raw.get("completion_tokens", 0))
    return (
        prompt if isinstance(prompt, int) and prompt >= 0 else 0,
        completion if isinstance(completion, int) and completion >= 0 else 0,
    )


def tool_arguments(raw: Any) -> dict[str, Any]:
    """A tool call's arguments as a dict; a JSON string is parsed, junk becomes ``{}``."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


class ProviderClient(ABC):
    """One provider's chat API behind :class:`~ezchain_agent_runtime.ModelClient`."""

    provider: ClassVar[str]
    credential_env: ClassVar[str]

    def __init__(
        self,
        *,
        model_id: str,
        pricing: ModelPricing,
        record: SessionRecord,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        logger: logging.Logger | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if not model_id.strip():
            raise ValueError("a provider client needs the model id it is pinned to")
        if record.model_id != model_id:
            raise ValueError(f"session record is pinned to {record.model_id!r}, not {model_id!r}")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._model_id = model_id
        self._pricing = pricing
        self._record = record
        self._transport: Transport = HttpxTransport() if transport is None else transport
        self._timeout = timeout
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        self._clock = clock

    @property
    def model_id(self) -> str:
        """The id this session is pinned to. Never re-read from the environment."""
        return self._model_id

    @property
    def record(self) -> SessionRecord:
        return self._record

    @abstractmethod
    def _endpoint(self) -> str: ...

    @abstractmethod
    def _headers(self, secret: str) -> dict[str, str]: ...

    @abstractmethod
    def _payload(self, request: ModelRequest) -> dict[str, Any]: ...

    @abstractmethod
    def _decode(self, body: Mapping[str, Any]) -> ModelTurn: ...

    def complete(self, request: ModelRequest) -> ModelTurn:
        secret = Credential.from_env(self.credential_env).secret()
        payload = dict(self._payload(request))
        payload["model"] = self._model_id
        http_request = HttpRequest(
            method="POST",
            url=self._endpoint(),
            headers=self._headers(secret),
            json=payload,
            timeout=self._timeout,
        )
        started = time.monotonic()
        response = self._transport(http_request)
        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status >= 400:
            raise ProviderHTTPError(
                self.provider, response.status, self._error_detail(response, secret)
            )
        decoded = self._decode(response.body)
        cost = self._pricing.cost(
            prompt_tokens=decoded.usage.prompt_tokens,
            completion_tokens=decoded.usage.completion_tokens,
        )
        turn = ModelTurn(
            text=decoded.text,
            tool_calls=decoded.tool_calls,
            answer=decoded.answer,
            usage=ModelUsage(
                prompt_tokens=decoded.usage.prompt_tokens,
                completion_tokens=decoded.usage.completion_tokens,
                cost_usd=cost,
            ),
        )
        self._record.add_turn(
            model_id=self._model_id,
            prompt_tokens=turn.usage.prompt_tokens,
            completion_tokens=turn.usage.completion_tokens,
            cost_usd=cost,
            tool_calls=len(turn.tool_calls),
            stop=_stop_reason(turn),
            latency_ms=latency_ms,
        )
        self._logger.info(
            "%s",
            redact(
                f"{self.provider} model={self._model_id} tokens={turn.usage.total_tokens} "
                f"cost_usd={cost} latency_ms={latency_ms} tool_calls={len(turn.tool_calls)}",
                secrets=(secret,),
            ),
        )
        return turn

    def _error_detail(self, response: HttpResponse, secret: str) -> str:
        body = response.body
        error = body.get("error")
        if isinstance(error, Mapping):
            detail = str(error.get("message") or error.get("type") or "")
        elif error is not None:
            detail = str(error)
        else:
            detail = str(body.get("message") or "")
        return redact(detail or "no error body", secrets=(secret,))[:500]

    @staticmethod
    def _require(body: Mapping[str, Any], key: str, kind: type[Any]) -> Any:
        value = body.get(key)
        if not isinstance(value, kind):
            raise ProviderProtocolError(f"the response has no usable {key!r}")
        return value


def _stop_reason(turn: ModelTurn) -> str:
    if turn.tool_calls:
        return "tool_use"
    if turn.answer is not None:
        return "answer"
    return "text"


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "LOGGER_NAME",
    "ProviderClient",
    "tool_arguments",
    "usage_from",
    "utcnow",
]
