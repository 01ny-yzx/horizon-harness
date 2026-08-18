"""Offline smoke coverage for the WebSearch execution contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.web_search_provider as provider_module
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_legacy_dict
from core.tool_outcome_resolution import resolve_tool_outcome
from core.web_search_provider import (
    WEB_SEARCH_MAX_RESPONSE_BYTES,
    WEB_SEARCH_REQUEST_TIMEOUT_SECONDS,
)
from tools.web_tools import WEB_TOOL_SCHEMAS, web_search


_UNSET = object()


class FakeTimeoutError(Exception):
    pass


class FakeInvalidAPIKeyError(Exception):
    pass


class FakeMissingAPIKeyError(Exception):
    pass


class FakeUsageLimitExceededError(Exception):
    pass


class FakeBadRequestError(Exception):
    pass


class FakeForbiddenError(Exception):
    pass


FAKE_EXCEPTION_TYPES = {
    "TavilyTimeoutError": FakeTimeoutError,
    "TavilyInvalidAPIKeyError": FakeInvalidAPIKeyError,
    "TavilyMissingAPIKeyError": FakeMissingAPIKeyError,
    "TavilyUsageLimitExceededError": FakeUsageLimitExceededError,
    "TavilyBadRequestError": FakeBadRequestError,
    "TavilyForbiddenError": FakeForbiddenError,
}


class FakeTavilyClient:
    calls: list[dict[str, Any]] = []
    response: Any = {"results": []}
    exception: Exception | None = None

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    def search(self, **kwargs: Any) -> Any:
        type(self).calls.append(dict(kwargs))
        if type(self).exception is not None:
            raise type(self).exception
        return type(self).response

    @classmethod
    def reset(
        cls,
        *,
        response: Any = _UNSET,
        exception: Exception | None = None,
    ) -> None:
        cls.calls = []
        cls.response = {"results": []} if response is _UNSET else response
        cls.exception = exception


class FakeRateLimiter:
    allowed = True

    def check_and_increment(self, user_id: str, feature: str) -> dict[str, Any]:
        return {
            "allowed": type(self).allowed,
            "user_id": user_id,
            "feature": feature,
        }


class ExecutionPatch:
    def __init__(self, *, rate_allowed: bool = True) -> None:
        self.rate_allowed = rate_allowed
        self.previous_env: dict[str, str | None] = {}
        self.original_client: Any = None
        self.original_limiter: Any = None
        self.original_exception_types: dict[str, Any] = {}

    def __enter__(self) -> None:
        for key, value in {
            "WEB_SEARCH_PROVIDER": "tavily",
            "TAVILY_API_KEY": "tvly-smoke-key",
        }.items():
            self.previous_env[key] = os.environ.get(key)
            os.environ[key] = value
        self.original_client = provider_module.TavilyClient
        self.original_limiter = provider_module.RateLimiter
        provider_module.TavilyClient = FakeTavilyClient
        provider_module.RateLimiter = FakeRateLimiter
        for name, fake_type in FAKE_EXCEPTION_TYPES.items():
            self.original_exception_types[name] = getattr(provider_module, name)
            setattr(provider_module, name, fake_type)
        FakeRateLimiter.allowed = self.rate_allowed

    def __exit__(self, *_: object) -> None:
        provider_module.TavilyClient = self.original_client
        provider_module.RateLimiter = self.original_limiter
        for name, original_type in self.original_exception_types.items():
            setattr(provider_module, name, original_type)
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _assert_observation(result: dict[str, Any], *, success: bool) -> Any:
    envelope = ToolCallEnvelope(
        call_id="web-search-call",
        provider_call_id="web-search-call",
        source=ToolCallSource.STRUCTURED,
        raw_name="web_search",
        tool_name="web_search",
        canonical_name="web_search",
        executable_name="web_search",
        raw_arguments="{}",
        parsed_arguments={},
        sanitized_arguments={},
        status=ToolCallStatus.EXECUTABLE,
    )
    observation = normalize_tool_result(envelope, result)
    assert observation.success is success
    assert observation.status == ("success" if success else "failed")
    if not success:
        assert observation.error_code == result["error_code"]
    return observation


def _serialized_response_size(response: Any) -> int:
    return len(
        json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _response_with_exact_size(size: int) -> dict[str, Any]:
    response = {"results": [{"content": ""}]}
    base_size = _serialized_response_size(response)
    assert size >= base_size
    response["results"][0]["content"] = "x" * (size - base_size)
    assert _serialized_response_size(response) == size
    return response


def test_strict_arguments_and_schema() -> None:
    FakeTavilyClient.reset()
    with ExecutionPatch():
        invalid_cases = (
            {"query": ""},
            {"query": "   "},
            {"query": 123},
            {"query": "query", "max_results": 0},
            {"query": "query", "max_results": 11},
            {"query": "query", "max_results": "5"},
            {"query": "query", "max_results": True},
        )
        for arguments in invalid_cases:
            result = web_search(**arguments)  # type: ignore[arg-type]
            assert result["success"] is False
            assert result["status"] == "failed"
            assert result["error_code"] == "invalid_arguments"
            assert result["data"] == {"code": "invalid_arguments"}
            _assert_observation(result, success=False)
        assert FakeTavilyClient.calls == []

        for max_results in (None, 1, 5, 10):
            result = (
                web_search("allowed query")
                if max_results is None
                else web_search("allowed query", max_results=max_results)
            )
            assert result["success"] is True
        assert [call["max_results"] for call in FakeTavilyClient.calls] == [
            5,
            1,
            5,
            10,
        ]

    schema = next(
        item["function"]["parameters"]
        for item in WEB_TOOL_SCHEMAS
        if item["function"]["name"] == "web_search"
    )
    assert schema["properties"]["query"]["minLength"] == 1
    max_schema = schema["properties"]["max_results"]
    assert max_schema["minimum"] == 1
    assert max_schema["maximum"] == 10
    assert max_schema["default"] == 5


def test_timeout_and_success_contract() -> None:
    FakeTavilyClient.reset(
        response={
            "results": [
                {
                    "title": "Example",
                    "url": "https://docs.example.org/guide",
                    "content": "Result",
                }
            ]
        }
    )
    with ExecutionPatch():
        result = web_search("example", max_results=1)
    assert result["success"] is True
    assert FakeTavilyClient.calls[0]["timeout"] == WEB_SEARCH_REQUEST_TIMEOUT_SECONDS == 25
    assert result["data"]["provider"] == "tavily"
    assert result["data"]["query"] == "example"
    assert result["data"]["result_count"] == 1
    assert len(result["data"]["results"]) == 1
    _assert_observation(result, success=True)
    assert {
        "active_provider",
        "configured_provider",
        "search_available",
        "fetch_url_available",
        "cached",
        "error_code",
        "reason",
        "metadata",
    }.isdisjoint(result["data"])

    FakeTavilyClient.reset(response={"results": []})
    with ExecutionPatch():
        empty = web_search("no matches")
    assert empty == {
        "success": True,
        "data": {
            "provider": "tavily",
            "query": "no matches",
            "result_count": 0,
            "results": [],
        },
    }
    _assert_observation(empty, success=True)


def test_malformed_provider_responses() -> None:
    malformed = (
        None,
        "not a mapping",
        [],
        {"results": "not a list"},
        {"results": [{"title": "valid"}, "invalid item"]},
        {"results": [{"content": object()}]},
    )
    for response in malformed:
        FakeTavilyClient.reset(response=response)
        with ExecutionPatch():
            result = web_search("malformed response")
        assert result["success"] is False
        assert result["status"] == "failed"
        assert result["error_code"] == "provider_response_invalid"
        _assert_observation(result, success=False)


def test_provider_response_size_boundary() -> None:
    at_limit = _response_with_exact_size(WEB_SEARCH_MAX_RESPONSE_BYTES)
    FakeTavilyClient.reset(response=at_limit)
    with ExecutionPatch():
        allowed = web_search("exact boundary", max_results=1)
    assert allowed["success"] is True
    assert allowed["data"]["result_count"] == 1

    oversized_content = "x" * (WEB_SEARCH_MAX_RESPONSE_BYTES + 1)
    oversized = {"results": [{"content": oversized_content}]}
    assert _serialized_response_size(oversized) > WEB_SEARCH_MAX_RESPONSE_BYTES
    FakeTavilyClient.reset(response=oversized)
    with ExecutionPatch():
        rejected = web_search("oversized response", max_results=1)
    assert rejected["success"] is False
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "provider_response_too_large"
    assert "results" not in rejected["data"]
    rendered_result = json.dumps(rejected, ensure_ascii=False)
    assert oversized_content not in rendered_result
    observation = _assert_observation(rejected, success=False)
    legacy_observation = observation_to_legacy_dict(observation)
    outcome = resolve_tool_outcome(
        task_state=None,
        tool_name="web_search",
        arguments={"query": "oversized response", "max_results": 1},
        observation=legacy_observation,
    )
    assert outcome.kind == "allow_continue"
    assert outcome.reason == "ordinary_tool_error_returns_control_to_assistant"
    assert outcome.failure_disposition == "ordinary_failure"
    assert legacy_observation.get("recoverable") is not True
    rendered_observation = json.dumps(
        observation.__dict__,
        ensure_ascii=False,
        default=str,
    )
    assert oversized_content not in rendered_observation


def test_typed_provider_failures() -> None:
    cases = (
        (FakeTimeoutError("timeout detail"), "provider_timeout"),
        (FakeInvalidAPIKeyError("invalid"), "provider_auth_error"),
        (FakeMissingAPIKeyError("missing"), "provider_auth_error"),
        (FakeUsageLimitExceededError("limited"), "provider_rate_limited"),
        (FakeBadRequestError("bad request"), "provider_bad_request"),
        (FakeForbiddenError("forbidden"), "provider_forbidden"),
        (RuntimeError("unknown"), "provider_error"),
    )
    for exception, error_code in cases:
        FakeTavilyClient.reset(exception=exception)
        with ExecutionPatch():
            result = web_search("provider failure")
        assert result["success"] is False
        assert result["status"] == "failed"
        assert result["error_code"] == error_code
        assert result["data"]["error_code"] == error_code
        _assert_observation(result, success=False)


def test_provider_exception_credentials_do_not_leak() -> None:
    secret = "tvly-SECRET-123"
    FakeTavilyClient.reset(
        exception=FakeInvalidAPIKeyError(f"Authorization=Bearer {secret}")
    )
    with ExecutionPatch():
        result = web_search("credential leak check")
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "provider_auth_error"
    assert result["error"] == (
        "Unable to search the web for credential leak check"
    )
    observation = _assert_observation(result, success=False)
    rendered = json.dumps(
        {"result": result, "observation": observation.__dict__},
        ensure_ascii=False,
        default=str,
    )
    assert secret not in rendered
    assert "Authorization=Bearer" not in rendered


def test_rate_limiter_prevents_provider_call() -> None:
    FakeTavilyClient.reset()
    with ExecutionPatch(rate_allowed=False):
        result = web_search("quota denied")
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "rate_limit_exceeded"
    assert FakeTavilyClient.calls == []
    _assert_observation(result, success=False)


def test_each_tool_call_attempts_provider_search() -> None:
    FakeTavilyClient.reset(response={"results": []})
    with ExecutionPatch():
        first = web_search("same query")
        second = web_search("same query")
    assert first["success"] is True
    assert second["success"] is True
    assert len(FakeTavilyClient.calls) == 2


def main() -> None:
    test_strict_arguments_and_schema()
    test_timeout_and_success_contract()
    test_malformed_provider_responses()
    test_provider_response_size_boundary()
    test_typed_provider_failures()
    test_provider_exception_credentials_do_not_leak()
    test_rate_limiter_prevents_provider_call()
    test_each_tool_call_attempts_provider_search()
    print("smoke_web_search_execution_contract ok")


if __name__ == "__main__":
    main()
