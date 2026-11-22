"""Declarative provider and model registry.

A session chooses one model id. The registry is the single place that says which wire
protocol speaks that model, which endpoint answers it, which stored credential authenticates
it and what it costs. There is one client per wire protocol; a provider is a data entry here,
so adding one needs no new client code and no new branch in the runtime.

Cost is a tier, not a promise. ``free`` needs no credential and is priced at zero, ``your_key``
uses a key the workspace stored in its encrypted secret store, and ``paid`` is billed through
an operator gateway. A paid entry that has no gateway is marked unavailable and the API
refuses to open a chat on it rather than inventing a bill.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .anthropic import AnthropicClient
from .openai import OpenAICompatibleClient
from .pricing import ModelPricing

Tier = Literal["free", "your_key", "paid"]


@dataclass(frozen=True)
class ProviderSpec:
    """One provider's wire, endpoint and credential configuration."""

    name: str
    wire: str
    label: str
    base_url: str
    env_name: str | None = None
    base_url_env: str | None = None
    auth_optional: bool = False
    kind: str = "hosted"
    note: str = ""


@dataclass(frozen=True)
class ModelSpec:
    """One selectable model id and the cost tier it belongs to."""

    id: str
    provider: str
    label: str
    tier: Tier
    pricing: ModelPricing
    available: bool = True


def _free() -> ModelPricing:
    return ModelPricing(input_per_million=Decimal("0"), output_per_million=Decimal("0"))


def _rate(input_per_million: str, output_per_million: str) -> ModelPricing:
    return ModelPricing(
        input_per_million=Decimal(input_per_million),
        output_per_million=Decimal(output_per_million),
    )


# One client per wire protocol. A provider names one of these; it never supplies a client.
WIRE_CLIENTS = {
    "anthropic": AnthropicClient,
    "openai": OpenAICompatibleClient,
}

PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="anthropic",
        wire="anthropic",
        label="Anthropic",
        base_url="https://api.anthropic.com",
        env_name="ANTHROPIC_API_KEY",
    ),
    ProviderSpec(
        name="openai",
        wire="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        env_name="OPENAI_API_KEY",
    ),
    ProviderSpec(
        name="openrouter",
        wire="openai",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        env_name="OPENROUTER_API_KEY",
    ),
    ProviderSpec(
        name="command_code",
        wire="openai",
        label="Command Code",
        base_url="http://localhost:8080/v1",
        base_url_env="EZCHAIN_COMMAND_CODE_BASE_URL",
        env_name="COMMAND_CODE_API_KEY",
    ),
    ProviderSpec(
        name="opencode",
        wire="openai",
        label="OpenCode",
        base_url="http://localhost:4096/v1",
        base_url_env="EZCHAIN_OPENCODE_BASE_URL",
        auth_optional=True,
        note="Free models through a local or operator-supplied endpoint.",
    ),
    ProviderSpec(
        name="ollama",
        wire="openai",
        label="Ollama",
        base_url="http://localhost:11434/v1",
        base_url_env="EZCHAIN_OLLAMA_BASE_URL",
        auth_optional=True,
        kind="local",
        note="Runs on this machine; nothing leaves it.",
    ),
    ProviderSpec(
        name="lm_studio",
        wire="openai",
        label="LM Studio",
        base_url="http://localhost:1234/v1",
        base_url_env="EZCHAIN_LM_STUDIO_BASE_URL",
        auth_optional=True,
        kind="local",
        note="Runs on this machine; nothing leaves it.",
    ),
    ProviderSpec(
        name="openai_compatible",
        wire="openai",
        label="OpenAI-compatible endpoint",
        base_url="http://localhost:8000/v1",
        base_url_env="EZCHAIN_OPENAI_COMPATIBLE_BASE_URL",
        env_name="OPENAI_COMPATIBLE_API_KEY",
        auth_optional=True,
    ),
    ProviderSpec(
        name="gateway",
        wire="openai",
        label="Operator gateway",
        base_url="http://localhost:9000/v1",
        base_url_env="EZCHAIN_GATEWAY_BASE_URL",
        auth_optional=True,
        note="Billed through the operator; not available until a gateway is configured.",
    ),
)

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="anthropic/claude-sonnet-4-5",
        provider="anthropic",
        label="Claude Sonnet",
        tier="your_key",
        pricing=_rate("3.00", "15.00"),
    ),
    ModelSpec(
        id="openai/gpt-4o-mini",
        provider="openai",
        label="GPT-4o mini",
        tier="your_key",
        pricing=_rate("0.15", "0.60"),
    ),
    ModelSpec(
        id="openrouter/llama-3.3-70b",
        provider="openrouter",
        label="Llama 3.3 70B",
        tier="your_key",
        pricing=_rate("0.59", "0.79"),
    ),
    ModelSpec(
        id="command_code/default",
        provider="command_code",
        label="Command Code default",
        tier="your_key",
        pricing=_rate("3.00", "15.00"),
    ),
    ModelSpec(
        id="opencode/hy4-flash-free",
        provider="opencode",
        label="Flash (free)",
        tier="free",
        pricing=_free(),
    ),
    ModelSpec(
        id="ollama/llama3.2",
        provider="ollama",
        label="Llama 3.2 (local)",
        tier="free",
        pricing=_free(),
    ),
    ModelSpec(
        id="lm_studio/local-model",
        provider="lm_studio",
        label="Local model",
        tier="free",
        pricing=_free(),
    ),
    ModelSpec(
        id="gateway/general",
        provider="gateway",
        label="Operator gateway model",
        tier="paid",
        pricing=_rate("1.00", "5.00"),
        available=False,
    ),
)


class UnknownProvider(LookupError):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown provider {name!r}")
        self.name = name


class UnknownModel(LookupError):
    def __init__(self, model_id: str) -> None:
        super().__init__(f"unknown model {model_id!r}")
        self.model_id = model_id


def list_providers(
    registry: Sequence[ProviderSpec] | None = None,
) -> tuple[ProviderSpec, ...]:
    return PROVIDERS if registry is None else tuple(registry)


def list_models(model_specs: Sequence[ModelSpec] | None = None) -> tuple[ModelSpec, ...]:
    return MODELS if model_specs is None else tuple(model_specs)


def get_provider(name: str, *, registry: Sequence[ProviderSpec] | None = None) -> ProviderSpec:
    for spec in list_providers(registry):
        if spec.name == name:
            return spec
    raise UnknownProvider(name)


def get_model(model_id: str, *, model_specs: Sequence[ModelSpec] | None = None) -> ModelSpec:
    for model in list_models(model_specs):
        if model.id == model_id:
            return model
    raise UnknownModel(model_id)


def resolve_base_url(spec: ProviderSpec, *, env: Mapping[str, str] | None = None) -> str:
    """The configured override wins; otherwise the entry's default endpoint is used."""
    if spec.base_url_env is None:
        return spec.base_url
    source = os.environ if env is None else env
    configured = source.get(spec.base_url_env, "").strip()
    return configured or spec.base_url


def model_catalog(
    *,
    model_specs: Sequence[ModelSpec] | None = None,
    registry: Sequence[ProviderSpec] | None = None,
) -> list[dict[str, object]]:
    """Serializable catalogue for a model list. It never holds a credential."""
    out: list[dict[str, object]] = []
    for model in list_models(model_specs):
        provider = get_provider(model.provider, registry=registry)
        out.append(
            {
                "id": model.id,
                "provider": provider.name,
                "label": model.label,
                "tier": model.tier,
                "available": model.available,
                "wire": provider.wire,
                "kind": provider.kind,
                "keyless": provider.env_name is None,
                "input_per_million": str(model.pricing.input_per_million),
                "output_per_million": str(model.pricing.output_per_million),
            }
        )
    return out


__all__ = [
    "MODELS",
    "PROVIDERS",
    "WIRE_CLIENTS",
    "ModelSpec",
    "ProviderSpec",
    "Tier",
    "UnknownModel",
    "UnknownProvider",
    "get_model",
    "get_provider",
    "list_models",
    "list_providers",
    "model_catalog",
    "resolve_base_url",
]
