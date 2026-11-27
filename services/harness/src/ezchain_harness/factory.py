"""One place builds a client and pins it to a model id.

A session's model id is chosen once, here, and handed to the client as a value. The
environment is read at that moment and never again: the running client does not consult it,
so a mid-session change cannot move the id that is sent or recorded. :data:`MODEL_ENV` is the
only variable that names a model, :func:`build` pins an explicitly chosen provider and model,
and :func:`build_model` resolves both from :mod:`~.registry` for a model id the registry knows.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from .pricing import ModelPricing
from .record import SessionRecord
from .registry import (
    WIRE_CLIENTS,
    ModelSpec,
    ProviderSpec,
    get_model,
    get_provider,
    list_models,
    model_catalog,
    resolve_base_url,
)
from .transport import Transport

MODEL_ENV = "EZCHAIN_AGENT_MODEL"

PROVIDERS = (
    "anthropic",
    "openai",
    "openrouter",
    "command_code",
    "opencode",
    "ollama",
    "lm_studio",
    "openai_compatible",
    "gateway",
)


def model_from_env(env: Mapping[str, str] | None = None) -> str:
    """The id a new session is pinned to, read once from the environment."""
    source = os.environ if env is None else env
    return source.get(MODEL_ENV, "").strip()


def build(
    *,
    provider: str,
    model_id: str,
    pricing: ModelPricing | None = None,
    session_id: str,
    transport: Transport | None = None,
    timeout: float | None = None,
    base_url: str | None = None,
    env_name: str | None | object = None,
    auth_optional: bool | None = None,
    registry: Sequence[ProviderSpec] | None = None,
    model_specs: Sequence[ModelSpec] | None = None,
) -> Any:
    """A client pinned to ``model_id``, built from the registry's provider configuration.

    ``pricing`` is optional when the model is in the registry: the registry's operator rate
    is then used. An explicit rate still wins, so an operator can price a model differently
    without changing the registry.
    """
    spec = get_provider(provider, registry=registry)
    if pricing is None:
        pricing = get_model(model_id, model_specs=model_specs).pricing
    client_cls = WIRE_CLIENTS[spec.wire]
    record = SessionRecord(session_id=session_id, provider=provider, model_id=model_id)
    options: dict[str, Any] = {
        "model_id": model_id,
        "pricing": pricing,
        "record": record,
        "transport": transport,
        "env_name": spec.env_name if env_name is None else env_name,
        "auth_optional": spec.auth_optional if auth_optional is None else auth_optional,
    }
    if timeout is not None:
        options["timeout"] = timeout
    resolved_url = base_url if base_url is not None else resolve_base_url(spec)
    if resolved_url:
        options["base_url"] = resolved_url
    return client_cls(**options)


def build_model(
    model_id: str,
    *,
    session_id: str,
    transport: Transport | None = None,
    timeout: float | None = None,
    base_url: str | None = None,
    pricing: ModelPricing | None = None,
    registry: Sequence[ProviderSpec] | None = None,
    model_specs: Sequence[ModelSpec] | None = None,
) -> Any:
    """Build the client for a registered model id. Unknown ids are refused."""
    model = get_model(model_id, model_specs=model_specs)
    return build(
        provider=model.provider,
        model_id=model.id,
        pricing=pricing or model.pricing,
        session_id=session_id,
        transport=transport,
        timeout=timeout,
        base_url=base_url,
        registry=registry,
        model_specs=model_specs,
    )


def list_catalog() -> list[dict[str, object]]:
    """The serialisable model catalogue for a model list."""
    return model_catalog()


__all__ = [
    "MODEL_ENV",
    "PROVIDERS",
    "build",
    "build_model",
    "list_catalog",
    "list_models",
    "model_from_env",
]
