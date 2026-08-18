"""Compatibility entrypoint for LLM providers."""

from __future__ import annotations

from typing import Any

from config.settings import settings
from providers.base import (
    LLMCallOptions,
    LLMChatResult,
    LLMConfig,
    LLMProvider,
    call_llm_chat_result,
)
from providers.factory import create_llm_provider


def create_llm_from_settings(runtime_settings: object = settings) -> LLMProvider:
    """Build an LLM provider from runtime settings."""

    return create_llm_provider(runtime_settings)


def build_default_llm() -> LLMProvider:
    """Build the default LLM provider used by CLI and API entrypoints."""

    return create_llm_from_settings(settings)


class DeepSeekLLM:
    """Backward-compatible wrapper for old imports.

    DeepSeek is treated as an OpenAI-compatible configuration alias. New code
    should call ``build_default_llm`` or ``create_llm_from_settings``.
    """

    def __init__(self) -> None:
        self._provider = build_default_llm()
        self.config = self._provider.config
        self.name = self._provider.name

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> Any:
        return self._provider.chat(messages, tools, options=options)

    def chat_result(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> LLMChatResult:
        return call_llm_chat_result(
            self._provider,
            messages=messages,
            tools=tools,
            options=options,
        )

    def get_status(self) -> dict[str, Any]:
        return self._provider.get_status()
