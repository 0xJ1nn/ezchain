"""The two supported providers, built from one place.

A session's model id is chosen once, here, and handed to the client as a value. The
environment is read at that moment and never again: the running client does not consult it,
so a mid-session change cannot move the id that is sent or recorded. :data:`MODEL_ENV` is the
only variable that names a model, and :func:`build` is the only constructor the runtime uses.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .anthropic import AnthropicClient
from .openai import OpenAICompatibleClient
from .pricing import ModelPricing
from .record import SessionRecord
from .transport import Transport

MODEL_ENV = "EZCHAIN_AGENT_MODEL"

PROVIDERS = ("anthropic", "openai")


def model_from_env(env: Mapping[str, str] | None = None) -> str:
    """The id a new session is pinned to, read once from the environment."""
    source = os.environ if env is None else env
    return source.get(MODEL_ENV, "").strip()


def build(
    *,
    provider: str,
    model_id: str,
    pricing: ModelPricing,
    session_id: str,
    transport: Transport | None = None,
    timeout: float | None = None,
    base_url: str | None = None,
) -> AnthropicClient | OpenAICompatibleClient:
    """A client pinned to ``model_id``, writing to a record pinned to the same id."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
    record = SessionRecord(session_id=session_id, provider=provider, model_id=model_id)
    options: dict[str, Any] = {
        "model_id": model_id,
        "pricing": pricing,
        "record": record,
        "transport": transport,
    }
    if timeout is not None:
        options["timeout"] = timeout
    if base_url is not None:
        options["base_url"] = base_url
    if provider == "anthropic":
        return AnthropicClient(**options)
    return OpenAICompatibleClient(**options)


__all__ = ["MODEL_ENV", "PROVIDERS", "build", "model_from_env"]
