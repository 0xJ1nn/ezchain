"""Build a pinned model client from a provider in the registry.

A session's model id is chosen once, here, and handed to the client as a value. The
environment is read at that moment and never again: the running client does not consult it,
so a mid-session change cannot move the id that is sent or recorded. :data:`MODEL_ENV` is the
only variable that names a model, and :func:`build` is the only constructor the runtime uses.

Adding a provider is a registry entry, not a branch here: the entry names the wire protocol,
the key variable and the endpoint. A local endpoint names no key variable, and its client
sends no credential.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .anthropic import AnthropicClient
from .openai import OpenAICompatibleClient
from .pricing import ModelPricing
from .providers import get as get_provider
from .providers import names as provider_names
from .record import SessionRecord
from .transport import Transport

MODEL_ENV = "EZCHAIN_AGENT_MODEL"


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
    env: Mapping[str, str] | None = None,
) -> AnthropicClient | OpenAICompatibleClient:
    """A client pinned to ``model_id``, configured from the provider registry."""
    spec = get_provider(provider)
    record = SessionRecord(session_id=session_id, provider=provider, model_id=model_id)
    options: dict[str, Any] = {
        "model_id": model_id,
        "pricing": pricing,
        "record": record,
        "transport": transport,
    }
    if timeout is not None:
        options["timeout"] = timeout
    source: Mapping[str, str] = os.environ if env is None else env
    url = base_url
    if url is None and spec.base_url_env is not None:
        url = source.get(spec.base_url_env) or None
    if url is None:
        url = spec.default_base_url
    if url is not None:
        options["base_url"] = url
    client: AnthropicClient | OpenAICompatibleClient = (
        AnthropicClient(**options)
        if spec.protocol == "anthropic"
        else OpenAICompatibleClient(**options)
    )
    # The wire client is shared; the provider name and key source come from configuration.
    client.provider = provider
    client.credential_env = spec.api_key_env
    return client


__all__ = ["MODEL_ENV", "build", "model_from_env", "provider_names"]
