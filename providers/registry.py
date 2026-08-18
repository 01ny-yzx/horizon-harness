"""Provider registry and alias resolution."""

from __future__ import annotations

from typing import Callable

from providers.base import LLMConfig, LLMProvider
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider


ProviderBuilder = Callable[[LLMConfig], LLMProvider]


PROVIDER_REGISTRY: dict[str, ProviderBuilder] = {
    "openai_compatible": OpenAICompatibleProvider,
    "mock": MockProvider,
}

PROVIDER_ALIASES: dict[str, str] = {
    "deepseek": "openai_compatible",
}


def resolve_provider_name(name: str) -> str:
    normalized = (name or "openai_compatible").strip().lower()
    return PROVIDER_ALIASES.get(normalized, normalized)


def get_provider_builder(name: str) -> ProviderBuilder:
    resolved = resolve_provider_name(name)
    try:
        return PROVIDER_REGISTRY[resolved]
    except KeyError as exc:
        available = ", ".join(sorted(set(PROVIDER_REGISTRY) | set(PROVIDER_ALIASES)))
        raise ValueError(f"Unknown LLM provider '{name}'. Available providers: {available}.") from exc
