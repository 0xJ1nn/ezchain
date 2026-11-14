"""ezCHAIN's agent harness: the model clients the runtime loop calls.

The runtime owns the loop, the budgets and the no-fake-data answers; this service owns the
providers. Two clients implement :class:`ezchain_agent_runtime.ModelClient` — Anthropic's
Messages API and any OpenAI-compatible chat-completions API — over an injected
:class:`~.transport.Transport`, so the suite runs the whole mapping with no key and no socket.

What a session can prove afterwards, from :class:`~.record.SessionRecord`:

- the exact model id it ran, which is a constructor argument and never re-read from the
  environment, so changing the environment mid-session changes nothing it sends;
- what each turn cost, priced with the operator's :class:`~.pricing.ModelPricing`;
- nothing about the credential. There is no key argument anywhere in this package: the only
  source is :class:`~.credentials.Credential`, which reads one environment variable and
  renders as ``[redacted]``.
"""

from __future__ import annotations

from .answer import ANSWER_CONTRACT, parse_answer
from .anthropic import AnthropicClient
from .base import DEFAULT_TIMEOUT_SECONDS, ProviderClient
from .credentials import Credential
from .errors import (
    CredentialError,
    MissingCredential,
    ProviderError,
    ProviderHTTPError,
    ProviderProtocolError,
    ProviderTimeout,
    ProviderTransportError,
)
from .factory import build, model_from_env
from .openai import OpenAICompatibleClient
from .pricing import ModelPricing
from .record import RECORD_SCHEMA, RecordedTurn, SessionRecord
from .redact import REDACTED, redact
from .transport import HttpRequest, HttpResponse, HttpxTransport, Transport

__all__ = [
    "ANSWER_CONTRACT",
    "DEFAULT_TIMEOUT_SECONDS",
    "RECORD_SCHEMA",
    "REDACTED",
    "AnthropicClient",
    "Credential",
    "CredentialError",
    "HttpRequest",
    "HttpResponse",
    "HttpxTransport",
    "MissingCredential",
    "ModelPricing",
    "OpenAICompatibleClient",
    "ProviderClient",
    "ProviderError",
    "ProviderHTTPError",
    "ProviderProtocolError",
    "ProviderTimeout",
    "ProviderTransportError",
    "RecordedTurn",
    "SessionRecord",
    "Transport",
    "build",
    "model_from_env",
    "parse_answer",
    "redact",
]
