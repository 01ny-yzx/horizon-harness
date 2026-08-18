"""Local mock LLM provider for tests and smoke checks."""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from providers.base import (
    LLMCallOptions,
    LLMCapabilities,
    LLMChatResult,
    LLMConfig,
    LLMUsage,
)
from providers.capabilities import default_capabilities_for_provider, reasoning_auto_policy


def assistant_message(content: str = "mock response", tool_calls: list[Any] | None = None) -> Any:
    """Build an OpenAI-message-shaped object used by AgentLoop."""

    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


class MockProvider:
    """Offline provider that returns deterministic assistant messages."""

    name = "mock"

    def __init__(self, config: LLMConfig | None = None, responses: Iterable[Any] | None = None) -> None:
        self.config = config or LLMConfig(provider="mock", model="mock")
        self.responses = list(responses or [assistant_message()])
        self.index = 0
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> Any:
        self.calls.append({"messages": messages, "tools": tools, "options": options})
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(response, str):
            return assistant_message(response)
        return response

    def chat_result(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> LLMChatResult:
        """Return the legacy mock response in the structured Provider envelope."""

        return LLMChatResult(
            message=self.chat(messages, tools, options=options),
            usage=LLMUsage(available=False),
        )

    def get_status(self) -> dict[str, Any]:
        return {
            **self.config.safe_dict(),
            "name": self.name,
            "api_key_required": False,
            "capabilities": self.capabilities.safe_dict(),
            "reasoning_auto_policy": reasoning_auto_policy(self.config.reasoning_mode, self.capabilities),
        }

    @property
    def capabilities(self) -> LLMCapabilities:
        return self.config.capabilities or default_capabilities_for_provider(self.config.provider)
