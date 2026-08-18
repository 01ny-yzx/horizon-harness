"""Verify fetch_url reports current-call facts without next-tool hints."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.final_observation_context import build_final_observation_context
from core.tool_outcome_resolution import ToolOutcomeResolution, resolve_tool_outcome
from tools.browser_tools import BROWSER_TOOL_SCHEMAS
import tools.web_tools as web_tools


FORBIDDEN = {"needs_browser_fallback", "fallback_reason", "fallback_url"}


class FakeResponse:
    def __init__(self, body: bytes, *, status_code: int = 200, content_type: str = "text/plain") -> None:
        self.body = body
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    async def aiter_bytes(self):
        yield self.body



class FakeStream:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return False


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        return FakeStream(self.response)


class DummyTaskState:
    def __init__(self) -> None:
        self.metadata = {}
        self.tool_failures = []
        self.task_type = "simple"
        self.user_goal = "read a URL"


def assert_neutral(value) -> None:
    if isinstance(value, dict):
        assert not FORBIDDEN & set(value), value
        for item in value.values():
            assert_neutral(item)
    elif isinstance(value, list):
        for item in value:
            assert_neutral(item)


def fetch(response: FakeResponse, **kwargs) -> dict:
    with patch.object(web_tools.httpx, "AsyncClient", return_value=FakeClient(response)):
        return web_tools.fetch_url("https://example.com/page", **kwargs)


def main() -> None:
    short = fetch(FakeResponse(b"short page"), format="text")
    error_looking = fetch(FakeResponse(b"404 Not Found"), format="text")
    for result in (short, error_looking):
        assert result["success"] is True
        assert result["data"]["output"]
        assert set(result["data"]) == {"url", "content_type", "format", "output"}
        assert_neutral(result)

    failure = {
        "success": False,
        "status": "failed",
        "error": "Unable to fetch https://example.com/page",
        "error_code": "http_error",
        "data": {"url": "https://example.com/page", "http_status": 403, "error_code": "http_error"},
    }
    outcome = resolve_tool_outcome(
        task_state=DummyTaskState(),
        tool_name="fetch_url",
        arguments={"url": "https://example.com/page"},
        observation=failure,
    )
    assert outcome.kind == "allow_continue"
    assert outcome.failure_disposition == "ordinary_failure"
    assert not hasattr(outcome, "next_tool")

    context = build_final_observation_context(
        DummyTaskState(),
        ToolOutcomeResolution(
            "allow_continue",
            "ordinary_tool_error",
            tool="fetch_url",
            metadata={"observation": failure},
        ),
    )
    summary = context[0]["data_summary"]
    assert summary["url"] == "https://example.com/page"
    assert summary["http_status"] == 403
    assert "source_type" not in summary and "is_official" not in summary
    assert_neutral(summary)

    schema = next(
        item["function"] for item in BROWSER_TOOL_SCHEMAS
        if item["function"]["name"] == "browser_extract_text"
    )
    description = schema["description"].lower()
    assert "playwright" in description and "rendered page" in description
    assert "fallback" not in description and "fetch_url" not in description
    print("smoke_fetch_url_neutral_observation ok")


if __name__ == "__main__":
    main()
