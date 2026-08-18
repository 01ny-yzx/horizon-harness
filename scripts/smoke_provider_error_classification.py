"""Offline structural checks for provider error classification."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.openai_compatible import (
    LLMProviderError,
    OpenAICompatibleProvider,
    _provider_error_from_exception,
)
from providers.base import LLMCapabilities, LLMConfig


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status={status_code}")
        self.status_code = status_code


class ResponseError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"response={status_code}")
        self.response = type("Response", (), {"status_code": status_code, "headers": {"retry-after": "1"}})()


class APIConnectionError(Exception):
    pass


def main() -> None:
    existing = LLMProviderError("config", code="configuration_error")
    assert existing.code == "configuration_error" and existing.retryable is False

    provider = OpenAICompatibleProvider(
        LLMConfig(
            provider="openai_compatible",
            model="mock",
            api_key="safe-test-key",
            capabilities=LLMCapabilities(supports_tools=True),
        )
    )
    provider.client = type(
        "Client",
        (),
        {
            "chat": type(
                "Chat",
                (),
                {"completions": type("Completions", (), {"create": lambda *_args, **_kwargs: (_ for _ in ()).throw(existing)})()},
            )(),
        },
    )()
    try:
        provider.chat(messages=[{"role": "user", "content": "x"}], tools=[])
    except LLMProviderError as exc:
        assert exc is existing
    else:
        raise AssertionError("existing LLMProviderError must be propagated")

    cases = (
        (StatusError(500), "provider_status_error", True, 500),
        (ResponseError(503), "provider_status_error", True, 503),
        (StatusError(429), "rate_limit", True, 429),
        (StatusError(401), "authentication_error", False, 401),
        (TimeoutError("timeout"), "timeout", True, None),
        (APIConnectionError("connection"), "connection_error", True, None),
        (RuntimeError("unknown"), "provider_error", False, None),
    )
    for exc, code, retryable, status_code in cases:
        result = _provider_error_from_exception(exc, str(exc))
        assert result.code == code, (exc, result.code)
        assert result.retryable is retryable, (exc, result.retryable)
        assert result.status_code == status_code, (exc, result.status_code)
    print("smoke_provider_error_classification ok")


if __name__ == "__main__":
    main()
