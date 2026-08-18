"""Smoke checks for provider per-call LLM profiles."""

from __future__ import annotations

import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# core.loop imports optional remote-MCP/web support; this smoke does not use it.
requests_stub = types.ModuleType("requests")
requests_stub.get = lambda *args, **kwargs: None
requests_stub.post = lambda *args, **kwargs: None
requests_stub.Session = lambda *args, **kwargs: None
requests_stub.exceptions = SimpleNamespace(RequestException=Exception, Timeout=TimeoutError)
sys.modules.setdefault("requests", requests_stub)

from core.llm_call_profile import resolve_llm_call_options
from providers.base import LLMCallOptions, LLMCapabilities, LLMConfig
from providers.openai_compatible import OpenAICompatibleProvider


PROFILE_ENV_KEYS = (
    "LLM_PROFILE_INITIAL_AGENT_TURN_MAX_TOKENS",
    "LLM_PROFILE_INITIAL_AGENT_TURN_TIMEOUT",
    "LLM_PROFILE_INITIAL_AGENT_TURN_TEMPERATURE",
    "LLM_PROFILE_INITIAL_AGENT_TURN_DISABLE_REASONING",
    "LLM_PROFILE_TOOL_CALL_MAX_TOKENS",
    "LLM_PROFILE_TOOL_CALL_TIMEOUT",
    "LLM_PROFILE_TOOL_CALL_TEMPERATURE",
    "LLM_PROFILE_TOOL_CALL_DISABLE_REASONING",
    "LLM_PROFILE_FINAL_ANSWER_MAX_TOKENS",
    "LLM_PROFILE_FINAL_ANSWER_TIMEOUT",
    "LLM_PROFILE_FINAL_ANSWER_TEMPERATURE",
    "LLM_PROFILE_FINAL_ANSWER_DISABLE_REASONING",
)


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **payload: Any) -> Any:
        self.calls.append(payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]))])


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions())


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> Any:
        self.calls.append({"messages": messages, "tools": tools, "options": options})
        return SimpleNamespace(content="{}", tool_calls=[])


@contextmanager
def cleared_profile_env() -> Any:
    old = {key: os.environ.get(key) for key in PROFILE_ENV_KEYS}
    for key in PROFILE_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _settings(timeout: float = 60.0, temperature: float = 0.7) -> SimpleNamespace:
    return SimpleNamespace(llm_timeout=timeout, llm_temperature=temperature)


def _capabilities(*, supports_json_mode: bool | None = True) -> LLMCapabilities:
    return LLMCapabilities(
        supports_tools=True,
        supports_reasoning=True,
        requires_reasoning_echo=True,
        supports_disable_reasoning=True,
        supports_extra_body=True,
        supports_json_mode=supports_json_mode,
    )


def test_profile_resolver_defaults() -> None:
    with cleared_profile_env():
        initial = resolve_llm_call_options("initial_agent_turn", _settings())
        assert initial.stage == "initial_agent_turn"
        assert initial.max_tokens == 1024
        assert initial.temperature == 0.0
        assert initial.timeout == 30.0
        assert initial.disable_reasoning is True
        assert initial.response_format is None

        tool_call = resolve_llm_call_options("tool_call", _settings())
        assert tool_call.disable_reasoning is True
        assert tool_call.max_tokens is not None

        final_answer = resolve_llm_call_options("final_answer", _settings())
        assert final_answer.stage == "final_answer"
        assert final_answer.max_tokens == 1024
        assert final_answer.timeout == 45.0
        assert final_answer.temperature == 0.2
        assert final_answer.disable_reasoning is True

        single_file = resolve_llm_call_options("single_file_read_final_answer", _settings())
        assert single_file.max_tokens == 1024
        assert single_file.temperature == 0.0


def test_final_answer_profile_env_override() -> None:
    with cleared_profile_env():
        os.environ["LLM_PROFILE_FINAL_ANSWER_MAX_TOKENS"] = "1400"
        final_answer = resolve_llm_call_options("final_answer", _settings())
        assert final_answer.max_tokens == 1400


def test_openai_compatible_per_call_payload() -> None:
    config = LLMConfig(
        provider="openai_compatible",
        model="same-model",
        api_key="test-key",
        temperature=0.7,
        timeout=60.0,
        reasoning_mode="enabled",
        extra_body={},
        capabilities=_capabilities(),
    )
    provider = OpenAICompatibleProvider(config)
    client = FakeClient()
    provider.client = client
    options = LLMCallOptions(
        stage="initial_agent_turn",
        max_tokens=123,
        temperature=0.0,
        timeout=5.0,
        disable_reasoning=True,
        response_format={"type": "json_object"},
    )

    provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[], options=options)
    payload = client.chat.completions.calls[-1]
    assert payload["model"] == "same-model"
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 123
    assert payload["timeout"] == 5.0
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["extra_body"]["thinking"]["type"] == "disabled"
    assert provider.config.model == "same-model"
    assert provider.config.api_key == "test-key"


def test_openai_compatible_options_none_keeps_config_defaults() -> None:
    config = LLMConfig(
        provider="openai_compatible",
        model="same-model",
        api_key="test-key",
        temperature=0.55,
        timeout=60.0,
        reasoning_mode="disabled",
        extra_body={},
        capabilities=_capabilities(),
    )
    provider = OpenAICompatibleProvider(config)
    client = FakeClient()
    provider.client = client

    provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
    payload = client.chat.completions.calls[-1]
    assert payload["model"] == "same-model"
    assert payload["temperature"] == 0.55
    assert "max_tokens" not in payload
    assert "timeout" not in payload
    assert payload["extra_body"]["thinking"]["type"] == "disabled"


def test_response_format_respects_capability_false() -> None:
    config = LLMConfig(
        provider="openai_compatible",
        model="same-model",
        api_key="test-key",
        capabilities=_capabilities(supports_json_mode=False),
    )
    provider = OpenAICompatibleProvider(config)
    client = FakeClient()
    provider.client = client
    options = LLMCallOptions(stage="initial_agent_turn", response_format={"type": "json_object"})

    provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[], options=options)
    payload = client.chat.completions.calls[-1]
    assert "response_format" not in payload


def main() -> None:
    test_profile_resolver_defaults()
    test_final_answer_profile_env_override()
    test_openai_compatible_per_call_payload()
    test_openai_compatible_options_none_keeps_config_defaults()
    test_response_format_respects_capability_false()
    print("smoke_provider_per_call_profile ok")


if __name__ == "__main__":
    main()
