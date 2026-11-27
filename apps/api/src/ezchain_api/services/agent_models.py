"""Model catalogue and one-turn chats over the registered providers.

The catalogue is the registry's model list annotated with whether this workspace already
holds a key. A chat opens exactly one model turn: the stored key, when one is needed, is
decrypted for the call, installed in a scoped context and used only as an outbound header.
It never enters the response, a log line, the recorded turn or the text returned to a caller.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from sqlalchemy.orm import Session

from ezchain_agent_runtime import Message, ModelRequest
from ezchain_harness import (
    Transport,
    build_model,
    get_model,
    get_provider,
    list_catalog,
    provide_secret,
    redact,
)
from ezchain_harness.registry import UnknownModel

from ..config import Settings
from ..models import new_id
from . import provider_keys
from .provider_secrets import SecretCipher


class ChatError(ValueError):
    """The chat cannot start. The caller can fix it without changing the model."""

    code = "MODEL_CHAT_ERROR"
    status_code = 422
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = {}


def transport_for_tests() -> Transport | None:
    """The dependency seam for the real HTTP transport. Tests inject a scripted one."""
    return None


def catalog(session: Session, settings: Settings) -> dict[str, Any]:
    """Every registered model with its cost tier and whether this workspace has its key."""
    models = list_catalog()
    for model in models:
        model["has_key"] = (
            provider_keys.has_active_key(
                session,
                workspace_id=settings.workspace_id,
                provider=str(model["provider"]),
            )
            if not model["keyless"]
            else False
        )
    return {"models": models}


def chat(
    session: Session,
    settings: Settings,
    cipher: SecretCipher,
    *,
    provider: str | None,
    model_id: str,
    prompt: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One model turn with the usage and cost the client recorded for it."""
    if not prompt.strip():
        raise ChatError("prompt must not be empty")
    try:
        model = get_model(model_id)
    except UnknownModel as exc:
        raise ChatError(f"unknown model {model_id!r}") from exc
    spec = get_provider(model.provider)
    if provider and provider != model.provider:
        raise ChatError(f"model {model_id} belongs to {model.provider}, not {provider}")
    if not model.available:
        raise ChatError(
            f"model {model_id} is billed through an operator gateway that is not configured"
        )
    if spec.env_name and not provider_keys.has_active_key(
        session, workspace_id=settings.workspace_id, provider=model.provider
    ):
        raise ChatError(f"add a key for {spec.label} before using {model_id}")

    client = build_model(model_id, session_id=new_id(), transport=transport)
    secret: str | None = None
    if spec.env_name:
        secret = provider_keys.resolve_secret(
            session,
            workspace_id=settings.workspace_id,
            provider=model.provider,
            cipher=cipher,
        )
    scope = provide_secret(spec.env_name, secret) if secret and spec.env_name else nullcontext()
    with scope:
        turn = client.complete(
            ModelRequest(messages=(Message(role="user", content=prompt),), tools=())
        )
    usage = turn.usage
    return {
        "provider": spec.name,
        "model_id": model.id,
        "tier": model.tier,
        "text": redact(turn.text, secrets=(secret,) if secret else ()),
        "summary": None if turn.answer is None else turn.answer.summary,
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost_usd": str(usage.cost_usd),
            "reported": usage.total_tokens > 0,
        },
    }


__all__ = ["ChatError", "catalog", "chat", "transport_for_tests"]
