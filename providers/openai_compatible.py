"""OpenAI-compatible chat completions provider."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.unicode_safety import sanitize_unicode
from providers.base import (
    LLMCallOptions,
    LLMCapabilities,
    LLMChatResult,
    LLMConfig,
    LLMUsage,
    extract_provider_metadata,
)
from providers.capabilities import default_capabilities_for_provider, reasoning_auto_policy


class LLMProviderError(RuntimeError):
    """Raised when an LLM provider cannot complete a request safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        status_code: int | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.response_headers = dict(response_headers or {})


def _exception_status_code(exc: BaseException) -> int | None:
    """Read a provider status code from compatible SDK exception shapes."""

    candidates = (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(exc, "response_status", None),
    )
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _exception_retryable(exc: BaseException) -> bool | None:
    """Read explicit SDK retryability without parsing error text."""

    for name in ("retryable", "is_retryable", "should_retry"):
        value = getattr(exc, name, None)
        if isinstance(value, bool):
            return value
    return None


def _exception_headers(exc: BaseException) -> dict[str, str]:
    """Keep only safe retry headers from a provider error response."""

    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not hasattr(headers, "get"):
        headers = getattr(exc, "headers", None)
    if not hasattr(headers, "get"):
        return {}
    result: dict[str, str] = {}
    for name in ("retry-after", "retry-after-ms"):
        value = headers.get(name)
        if value is not None:
            result[name] = str(value)
    return result


def _provider_error_from_exception(
    exc: BaseException,
    safe_error: str,
) -> LLMProviderError:
    """Classify compatible provider exceptions by structured transport state."""

    error_name = exc.__class__.__name__
    status_code = _exception_status_code(exc)
    headers = _exception_headers(exc)
    sdk_retryable = _exception_retryable(exc)
    if error_name == "AuthenticationError" or status_code in {401, 403}:
        return LLMProviderError(
            "LLM authentication failed. Check LLM_API_KEY for the configured provider.",
            code="authentication_error",
            status_code=status_code,
            response_headers=headers,
        )
    if error_name == "RateLimitError" or status_code == 429:
        return LLMProviderError(
            "LLM provider rate limit exceeded.",
            code="rate_limit",
            retryable=True,
            status_code=status_code or 429,
            response_headers=headers,
        )
    if error_name in {"APITimeoutError", "TimeoutError"} or isinstance(exc, TimeoutError) or status_code == 408:
        return LLMProviderError(
            "LLM request timed out.",
            code="timeout",
            retryable=True,
            status_code=status_code,
            response_headers=headers,
        )
    if error_name == "APIConnectionError":
        return LLMProviderError(
            "LLM network request failed. Check LLM_BASE_URL and network connectivity.",
            code="connection_error",
            retryable=True,
            status_code=status_code,
            response_headers=headers,
        )
    if status_code in {409, 425} or bool(status_code and status_code >= 500):
        return LLMProviderError(
            f"LLM provider returned an error status: {status_code}. {safe_error}",
            code="provider_status_error",
            retryable=True,
            status_code=status_code,
            response_headers=headers,
        )
    if sdk_retryable is True:
        return LLMProviderError(
            f"LLM request failed: {safe_error}",
            code="provider_error",
            retryable=True,
            status_code=status_code,
            response_headers=headers,
        )
    if status_code is not None and 400 <= status_code < 500:
        return LLMProviderError(
            f"LLM provider returned an error status: {status_code}. {safe_error}",
            code="provider_status_error",
            retryable=False,
            status_code=status_code,
            response_headers=headers,
        )
    return LLMProviderError(
        f"LLM request failed: {safe_error}",
        code="provider_error",
        retryable=False,
        status_code=status_code,
        response_headers=headers,
    )


def _usage_value(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _extract_usage(raw_usage: Any) -> LLMUsage:
    """Extract OpenAI-compatible usage fields without estimating absent usage."""

    if raw_usage is None:
        return LLMUsage()
    input_tokens = _usage_value(raw_usage, "prompt_tokens")
    output_tokens = _usage_value(raw_usage, "completion_tokens")
    total_tokens = _usage_value(raw_usage, "total_tokens")
    output_details = _usage_value(raw_usage, "completion_tokens_details")
    input_details = _usage_value(raw_usage, "prompt_tokens_details")
    reasoning_tokens = _usage_value(output_details, "reasoning_tokens")
    cached_tokens = _usage_value(input_details, "cached_tokens")
    usage = LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cached_tokens,
        available=True,
    )
    if total_tokens is None:
        usage = LLMUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.input_tokens + usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            available=True,
        )
    return usage


class OpenAICompatibleProvider:
    """Provider for any OpenAI-compatible Chat Completions API."""

    name = "openai_compatible"

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = None

    def chat_result(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> LLMChatResult:
        """Send one chat completion request and preserve safe turn metadata."""

        self._validate_config()
        disable_reasoning = self._should_disable_reasoning_for_request(messages, tools, options=options)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._normalize_messages_for_api(messages, disable_reasoning=disable_reasoning),
            "temperature": self.config.temperature if options is None or options.temperature is None else options.temperature,
        }
        if options is not None and options.max_tokens is not None:
            catalog_output = self.capabilities.max_output_tokens
            payload["max_tokens"] = min(options.max_tokens, catalog_output) if catalog_output else options.max_tokens
        if options is not None and options.response_format and self.capabilities.supports_json_mode is not False:
            payload["response_format"] = options.response_format
        if tools and self.capabilities.supports_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        extra_body = self._build_extra_body(disable_reasoning=disable_reasoning)
        if extra_body:
            payload["extra_body"] = extra_body
        payload = sanitize_unicode(payload)

        try:
            client = self._client()
            if options is not None and options.timeout is not None:
                response = client.chat.completions.create(**payload, timeout=options.timeout)
            else:
                response = client.chat.completions.create(**payload)
        except LLMProviderError:
            raise
        except Exception as exc:
            safe_error = self._safe_error(exc)
            if "reasoning_content" in safe_error:
                raise LLMProviderError(
                    "LLM provider requires reasoning_content to be echoed back. "
                    "Try setting LLM_REASONING_MODE=disabled for tool-calling tasks, "
                    f"or keep LLM_REASONING_MODE=enabled/auto so reasoning metadata echo can be used. Provider error: {safe_error}",
                    code="invalid_provider_response",
                ) from exc
            raise _provider_error_from_exception(exc, safe_error) from exc

        try:
            choice = response.choices[0]
            message = choice.message
            finish_reason = str(getattr(choice, "finish_reason", "") or "")
            return LLMChatResult(
                message=message,
                usage=_extract_usage(getattr(response, "usage", None)),
                finish_reason=finish_reason,
                provider_metadata={
                    **extract_provider_metadata(message),
                    "provider_finish_reason": finish_reason,
                },
            )
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMProviderError("LLM provider response did not include a compatible assistant message.", code="invalid_provider_response") from exc

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> Any:
        """Compatibility entrypoint returning the bare assistant message."""

        return self.chat_result(messages, tools, options=options).message

    def get_status(self) -> dict[str, Any]:
        return {
            **self.config.safe_dict(),
            "name": self.name,
            "api_key_required": True,
            "capabilities": self.capabilities.safe_dict(),
            "reasoning_auto_policy": reasoning_auto_policy(self.config.reasoning_mode, self.capabilities),
        }

    @property
    def capabilities(self) -> LLMCapabilities:
        return self.config.capabilities or default_capabilities_for_provider(self.config.provider)

    def _validate_config(self) -> None:
        if not self.config.model.strip():
            raise LLMProviderError("LLM_MODEL is required for the configured provider.", code="configuration_error")
        if not self.config.api_key.strip():
            raise LLMProviderError("LLM_API_KEY is required for the configured provider.", code="configuration_error")

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMProviderError("The openai Python package is required for openai_compatible providers.", code="configuration_error") from exc
        self.client = OpenAI(
            api_key=self.config.api_key or "missing-api-key",
            base_url=self.config.base_url or None,
            timeout=self.config.timeout,
        )
        return self.client

    def _build_extra_body(self, disable_reasoning: bool | None = None) -> dict[str, Any]:
        if not self.capabilities.supports_extra_body:
            return {}
        extra_body = deepcopy(self.config.extra_body) if self.config.extra_body else {}
        should_disable = self.config.reasoning_mode == "disabled" if disable_reasoning is None else disable_reasoning
        if should_disable:
            thinking = extra_body.get("thinking")
            if not isinstance(thinking, dict):
                thinking = {}
            thinking = {**thinking, "type": "disabled"}
            extra_body["thinking"] = thinking
        return sanitize_unicode(extra_body)

    def _normalize_messages_for_api(
        self,
        messages: list[dict[str, Any]],
        disable_reasoning: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Convert internal message metadata into provider API fields."""

        should_disable = self.config.reasoning_mode == "disabled" if disable_reasoning is None else disable_reasoning
        normalized: list[dict[str, Any]] = []
        for original in messages:
            message = deepcopy(original)
            provider_metadata = message.pop("provider_metadata", None)
            message.pop("metadata", None)

            if (
                message.get("role") == "assistant"
                and not should_disable
                and self.capabilities.supports_reasoning
                and isinstance(provider_metadata, dict)
                and provider_metadata.get("reasoning_content")
            ):
                message["reasoning_content"] = provider_metadata["reasoning_content"]

            normalized.append(message)
        return sanitize_unicode(normalized)

    def _should_disable_reasoning_for_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> bool:
        if options is not None and options.disable_reasoning is not None:
            return bool(options.disable_reasoning)
        if self.config.reasoning_mode == "disabled":
            return self.capabilities.supports_disable_reasoning and self.capabilities.supports_extra_body
        if self.config.reasoning_mode == "enabled":
            return False
        return reasoning_auto_policy(self.config.reasoning_mode, self.capabilities, has_tools=bool(tools)) == "auto_disable_for_tools"

    def _safe_error(self, exc: Exception) -> str:
        text = sanitize_unicode(str(exc))
        api_key = self.config.api_key.strip()
        if api_key:
            text = text.replace(api_key, "[REDACTED]")
        return text[:500]
