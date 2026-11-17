"""The model-provider registry.

A provider is configuration: a name, a wire protocol, where its endpoint and key come from,
and what the model list should call it. Adding one is a :func:`register` call, not a new
client class — providers that speak the same protocol share the same client.

A key is never a field here. The client reads it from the environment only, and a local
endpoint names no key variable at all.
"""

from __future__ import annotations

from dataclasses import dataclass

Protocol = str  # "anthropic" or "openai" (OpenAI-compatible chat completions)
Billing = str  # "free", "your_key" or "paid"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    title: str
    protocol: Protocol
    billing: Billing
    api_key_env: str | None = None
    base_url_env: str | None = None
    default_base_url: str | None = None

    @property
    def label(self) -> str:
        return {"free": "free", "your_key": "your key", "paid": "paid"}[self.billing]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "title": self.title,
            "protocol": self.protocol,
            "billing": self.billing,
            "label": self.label,
            "key_required": self.api_key_env is not None,
            "api_key_env": self.api_key_env,
            "base_url_env": self.base_url_env,
            "default_base_url": self.default_base_url,
        }


_SPECS: dict[str, ProviderSpec] = {}


def register(spec: ProviderSpec) -> ProviderSpec:
    if spec.protocol not in ("anthropic", "openai"):
        raise ValueError(f"unknown wire protocol {spec.protocol!r}")
    if spec.billing not in ("free", "your_key", "paid"):
        raise ValueError(f"unknown billing label {spec.billing!r}")
    if spec.name in _SPECS:
        raise ValueError(f"provider already registered: {spec.name}")
    _SPECS[spec.name] = spec
    return spec


def unregister(name: str) -> None:
    _SPECS.pop(name, None)


def get(name: str) -> ProviderSpec:
    try:
        return _SPECS[name]
    except KeyError as exc:
        raise ValueError(f"unknown provider {name!r}") from exc


def names() -> tuple[str, ...]:
    return tuple(_SPECS)


def model_catalogue() -> list[dict[str, object]]:
    return [spec.to_dict() for spec in _SPECS.values()]


# The built-in set. Every provider below speaks one of the two existing wire protocols.
for _spec in (
    ProviderSpec(
        "anthropic",
        "Anthropic",
        "anthropic",
        "your_key",
        api_key_env="ANTHROPIC_API_KEY",
        default_base_url="https://api.anthropic.com",
    ),
    ProviderSpec(
        "openai",
        "OpenAI-compatible",
        "openai",
        "your_key",
        api_key_env="OPENAI_API_KEY",
        default_base_url="https://api.openai.com/v1",
    ),
    ProviderSpec(
        "opencode_zen",
        "OpenCode",
        "openai",
        "paid",
        api_key_env="OPENCODE_API_KEY",
        base_url_env="EZCHAIN_OPENCODE_ZEN_URL",
        default_base_url="https://opencode.ai/zen/v1",
    ),
    ProviderSpec(
        "opencode_go",
        "OpenCode Go",
        "openai",
        "free",
        api_key_env="OPENCODE_API_KEY",
        base_url_env="EZCHAIN_OPENCODE_GO_URL",
        default_base_url="https://opencode.ai/go/v1",
    ),
    ProviderSpec(
        "openrouter",
        "OpenRouter",
        "openai",
        "your_key",
        api_key_env="OPENROUTER_API_KEY",
        base_url_env="EZCHAIN_OPENROUTER_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    ProviderSpec(
        "command_code",
        "Command Code",
        "openai",
        "paid",
        api_key_env="COMMAND_CODE_API_KEY",
        base_url_env="EZCHAIN_COMMAND_CODE_URL",
        default_base_url="https://api.commandcode.com/v1",
    ),
    ProviderSpec(
        "openai_compatible",
        "Custom endpoint",
        "openai",
        "your_key",
        api_key_env="OPENAI_API_KEY",
        base_url_env="EZCHAIN_OPENAI_BASE_URL",
    ),
    ProviderSpec(
        "ollama",
        "Ollama",
        "openai",
        "free",
        base_url_env="EZCHAIN_OLLAMA_URL",
        default_base_url="http://localhost:11434/v1",
    ),
    ProviderSpec(
        "lm_studio",
        "LM Studio",
        "openai",
        "free",
        base_url_env="EZCHAIN_LM_STUDIO_URL",
        default_base_url="http://localhost:1234/v1",
    ),
):
    register(_spec)

__all__ = [
    "Billing",
    "Protocol",
    "ProviderSpec",
    "get",
    "model_catalogue",
    "names",
    "register",
    "unregister",
]
