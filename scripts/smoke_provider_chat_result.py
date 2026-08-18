"""Offline compatibility checks for complete Provider chat results."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import DeepSeekLLM
from providers.base import LLMCapabilities, LLMChatResult, LLMConfig
from providers.mock import MockProvider, assistant_message
from providers.openai_compatible import OpenAICompatibleProvider


class FakeCompletions:
    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def create(self, **_kwargs):
        return self.responses.pop(0)


def _provider(responses):
    provider = OpenAICompatibleProvider(
        LLMConfig(
            provider="openai_compatible",
            model="mock",
            api_key="safe-test-key",
            capabilities=LLMCapabilities(supports_tools=True),
        )
    )
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions(responses))
    )
    return provider


def _response(*, finish_reason="stop", usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=[]),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


class ChatOnlyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, options=None):
        self.calls += 1
        return assistant_message("compat")


def main() -> None:
    provider = _provider([
        _response(usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=3,
            total_tokens=14,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
            prompt_tokens_details=SimpleNamespace(cached_tokens=4),
        )),
        _response(finish_reason="length", usage=SimpleNamespace(
            prompt_tokens=7,
            completion_tokens=2,
            total_tokens=None,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )),
        _response(usage=None),
        _response(usage=None),
    ])
    first = provider.chat_result([{"role": "user", "content": "x"}], [])
    assert isinstance(first, LLMChatResult)
    assert first.message.content == "ok"
    assert first.usage.available is True
    assert (first.usage.input_tokens, first.usage.output_tokens, first.usage.total_tokens) == (11, 3, 14)
    assert (first.usage.reasoning_tokens, first.usage.cache_read_tokens) == (2, 4)
    assert first.finish_reason == "stop"

    second = provider.chat_result([{"role": "user", "content": "x"}], [])
    assert second.finish_reason == "length"
    assert second.usage.total_tokens == 9

    missing = provider.chat_result([{"role": "user", "content": "x"}], [])
    assert missing.usage.available is False
    assert missing.usage.total_tokens == 0

    message = provider.chat([{"role": "user", "content": "x"}], [])
    assert message.content == "ok"

    wrapper = object.__new__(DeepSeekLLM)
    wrapper._provider = _provider([_response(usage=None)])
    forwarded = wrapper.chat_result([{"role": "user", "content": "x"}], [])
    assert forwarded.message.content == "ok"

    mock = MockProvider(responses=[assistant_message("mock")])
    mock_result = mock.chat_result([{"role": "user", "content": "x"}], [])
    assert isinstance(mock_result, LLMChatResult)
    assert mock_result.message.content == "mock"
    assert mock_result.usage.available is False
    assert len(mock.calls) == 1

    chat_only = ChatOnlyProvider()
    wrapper._provider = chat_only
    compatible = wrapper.chat_result([{"role": "user", "content": "x"}], [])
    assert isinstance(compatible, LLMChatResult)
    assert compatible.message.content == "compat"
    assert compatible.usage.available is False
    assert chat_only.calls == 1
    print("smoke_provider_chat_result ok")


if __name__ == "__main__":
    main()
