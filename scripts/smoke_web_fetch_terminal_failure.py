"""Verify WebFetch failures remain ordinary assistant-owned observations."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_boundary import local_file_path_argument_guard
from core.tool_outcome_resolution import resolve_tool_outcome
from tools.file_tools import FILE_TOOL_SCHEMAS
from tools.web_tools import WEB_TOOL_SCHEMAS, fetch_url
import tools.web_tools as web_tools


class FakeResponse:
    status_code = 200
    headers = {"content-type": "text/plain"}

    async def aiter_bytes(self):
        yield b"ok"


class FakeStream:
    async def __aenter__(self):
        return FakeResponse()

    async def __aexit__(self, *_args):
        return False


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeStream()


class DummyTaskState:
    def __init__(self) -> None:
        self.user_goal = "请读取 ftp://example.com/file.txt"
        self.metadata = {
            "tool_plan": {
                "primary_tool": "fetch_url",
                "primary_capability": "web_fetch",
                "tool_priority": ["fetch_url"],
            }
        }
        self.tool_failures = []
        self.task_type = "simple"


def test_invalid_scheme_is_ordinary_failure() -> None:
    for url in (
        "ftp://example.com/file.txt",
        "file:///tmp/file.txt",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "custom://example.com/value",
    ):
        result = fetch_url(url)
        assert result["success"] is False
        assert result["status"] == "failed"
        assert result["error_code"] == "invalid_arguments"
        assert "policy_code" not in result
        outcome = resolve_tool_outcome(
            task_state=DummyTaskState(),
            tool_name="fetch_url",
            arguments={"url": url},
            observation=result,
        )
        assert outcome.kind == "allow_continue"
        assert outcome.failure_disposition == "ordinary_failure"
        assert outcome.policy_code == ""


def test_private_urls_are_executed() -> None:
    for url in (
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://192.168.1.10",
        "http://10.0.0.5",
        "http://[::1]:8080",
    ):
        client = FakeClient()
        with patch.object(web_tools.httpx, "AsyncClient", return_value=client):
            result = fetch_url(url, format="text")
        assert result["success"] is True
        assert client.calls[0]["url"] == url


def test_failure_has_no_runtime_fallback() -> None:
    observation = {
        "success": False,
        "status": "failed",
        "error": "Unable to fetch https://example.com",
        "error_code": "http_error",
        "data": {"url": "https://example.com", "http_status": 403},
    }
    state = DummyTaskState()
    state.user_goal = "请读取 https://example.com"
    outcome = resolve_tool_outcome(
        task_state=state,
        tool_name="fetch_url",
        arguments={"url": "https://example.com"},
        observation=observation,
    )
    assert outcome.kind == "allow_continue"
    assert outcome.failure_disposition == "ordinary_failure"
    assert not hasattr(outcome, "next_tool")
    assert not hasattr(outcome, "next_arguments")


def test_local_file_boundary_and_schema() -> None:
    for path in (
        "ftp://example.com/file.txt", "https://example.com", "file:///tmp/file.txt",
        "data:text/plain,hello", "javascript:alert(1)", "custom://example.com/value",
    ):
        blocked = local_file_path_argument_guard("read_file", {"path": path})
        assert blocked is not None
        assert blocked["data"]["code"] == "invalid_local_file_path"

    schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in [*FILE_TOOL_SCHEMAS, *WEB_TOOL_SCHEMAS]
    }
    assert "local filesystem path only" in schemas["read_file"]["description"]
    assert "HTTP or HTTPS URL" in schemas["fetch_url"]["description"]
    properties = schemas["fetch_url"]["parameters"]["properties"]
    assert set(properties) == {"url", "format", "timeout"}


def main() -> None:
    test_invalid_scheme_is_ordinary_failure()
    test_private_urls_are_executed()
    test_failure_has_no_runtime_fallback()
    test_local_file_boundary_and_schema()
    print("smoke_web_fetch_terminal_failure ok")


if __name__ == "__main__":
    main()
