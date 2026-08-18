"""Offline closure for URL-task tool-selection authority."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

_ENV = {
    "AGENT_ACCESS_MODE": "full_access",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "MCP_ENABLED": "false",
    "EMBEDDING_ENABLED": "false",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_boundary import evaluate_tool_execution_boundary
from core.state import TaskState
from core.task_profile import TaskProfile
from core.tool_call_grants import register_tool_call_grant
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from scripts.smoke_tool_result_llm_loop_reset import _call, _failed, _message, _run
from tools.registry import get_tool_spec
import tools.web_tools as web_tools


COMMAND = "echo URL_EXECUTION_AUTHORITY_OK"
URL = "https://example.com"


@contextmanager
def _access_mode(value: str):
    previous = os.environ.get("AGENT_ACCESS_MODE")
    os.environ["AGENT_ACCESS_MODE"] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous


def _url_state() -> TaskState:
    profile = TaskProfile(
        task_type="research",
        needs_web=True,
        has_url=True,
        has_search_engine_url=False,
        needs_code_edit=False,
        needs_validation=False,
        needs_git=False,
        user_intent_summary="read URL",
        needs_fetch_url=True,
        provided_urls=[URL],
        tool_required=True,
        side_effect_required=False,
    )
    state = TaskState.create(
        user_goal=f"读取 {URL}",
        task_type="research",
        plan=[],
        task_profile=profile,
    )
    state.metadata["tool_plan"] = {
        "primary_capability": "web_fetch",
        "primary_tool": "fetch_url",
        "tool_priority": ["fetch_url"],
        "supporting_capabilities": ["web_fetch"],
    }
    state.metadata["capability_routing"] = {
        "required_capabilities": ["web_fetch"],
        "tool_plan": dict(state.metadata["tool_plan"]),
    }
    return state


def _envelope(call_id: str, name: str, arguments: dict[str, Any]) -> ToolCallEnvelope:
    spec = get_tool_spec(name)
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name=name,
        tool_name=name,
        canonical_name=str(getattr(spec, "canonical_name", "") or name),
        executable_name=name,
        raw_arguments=json.dumps(arguments, ensure_ascii=False),
        parsed_arguments=dict(arguments),
        sanitized_arguments={},
        status=ToolCallStatus.EXECUTABLE,
        metadata={
            "tool_spec_found": True,
            "execution_grant_required": True,
            "grant_registered_arguments": dict(arguments),
        },
    )


def _register(state: TaskState, envelope: ToolCallEnvelope) -> None:
    decision = register_tool_call_grant(
        state,
        call_id=envelope.call_id,
        provider_call_id=envelope.provider_call_id,
        canonical_name=envelope.canonical_name,
        executable_name=envelope.executable_name,
        arguments=envelope.parsed_arguments,
        source=envelope.source,
        raw_arguments=envelope.raw_arguments,
    )
    assert decision.allowed is True, decision


class _RejectUnexpectedBrowserPolicy:
    calls = 0

    def check_url(self, _url: str) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("BrowserPolicy must not run for sandbox_exec")


class _LocalBlockingBrowserPolicy:
    calls = 0

    def check_url(self, url: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "allowed": False,
            "code": "blocked_localhost",
            "reason": f"browser local URL blocked: {url}",
        }


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "text/plain"}

    async def aiter_bytes(self):
        yield b"local ok"


class _FakeStream:
    async def __aenter__(self):
        return _FakeResponse()

    async def __aexit__(self, *_args: Any):
        return False


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any):
        return False

    def stream(self, method: str, url: str, **kwargs: Any):
        self.calls.append((method, url, kwargs))
        return _FakeStream()


def test_full_loop_fetch_failure_then_sandbox() -> None:
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("fetch-fail", "fetch_url", {"url": URL})]),
            _message("", [_call("shell-after-fetch", "sandbox_exec", {"command": COMMAND})]),
            _message("URL execution authority verified."),
        ],
        tools={
            "fetch_url": lambda **_: _failed("connection_failed", recoverable=True),
            "sandbox_exec": lambda **_: {
                "success": True,
                "status": "success",
                "data": {"stdout": "URL_EXECUTION_AUTHORITY_OK\n", "exit_code": 0},
            },
        },
        request=f"读取 {URL}",
    )
    assert answer == "URL execution authority verified."
    assert [name for name, _ in executed] == ["fetch_url", "sandbox_exec"]
    assert len(llm.calls) == 3
    observations = captured["state"].metadata.get("completion_observations") or []
    assert [item.get("call_id") for item in observations] == ["fetch-fail", "shell-after-fetch"]
    rendered = json.dumps(llm.calls[2]["messages"], ensure_ascii=False)
    assert "connection_failed" in rendered
    assert "URL_EXECUTION_AUTHORITY_OK" in rendered
    assert captured["state"].is_finished is True


def test_url_state_does_not_block_granted_sandbox() -> None:
    state = _url_state()
    envelope = _envelope("sandbox-url-task", "sandbox_exec", {"command": COMMAND})
    _register(state, envelope)
    policy = _RejectUnexpectedBrowserPolicy()
    with _access_mode("full_access"):
        decision = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="sandbox_exec",
            arguments=envelope.parsed_arguments,
            tool_call_envelope=envelope,
            raw_arguments=envelope.raw_arguments,
            browser_policy=policy,
        )
    assert decision.allowed is True, decision.to_dict()
    assert decision.boundary != "browser_url"
    assert policy.calls == 0

    search_envelope = _envelope(
        "search-url-task",
        "web_search",
        {"query": "example", "max_results": 1},
    )
    _register(state, search_envelope)
    with _access_mode("full_access"):
        search_decision = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="web_search",
            arguments=search_envelope.parsed_arguments,
            tool_call_envelope=search_envelope,
            raw_arguments=search_envelope.raw_arguments,
            browser_policy=policy,
        )
    assert search_decision.allowed is True, search_decision.to_dict()
    assert policy.calls == 0


def test_read_only_still_blocks_sandbox() -> None:
    state = _url_state()
    envelope = _envelope("sandbox-read-only", "sandbox_exec", {"command": COMMAND})
    _register(state, envelope)
    with _access_mode("read_only"):
        decision = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="sandbox_exec",
            arguments=envelope.parsed_arguments,
            tool_call_envelope=envelope,
            raw_arguments=envelope.raw_arguments,
        )
    assert decision.allowed is False
    assert decision.code == "agent_access_mode_read_only"


def test_fetch_local_urls_keep_their_own_contract() -> None:
    for url in (
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://192.168.1.20",
    ):
        fake = _FakeAsyncClient()
        with patch.object(web_tools.httpx, "AsyncClient", return_value=fake):
            result = web_tools.fetch_url(url, format="text")
        assert result["success"] is True, result
        assert fake.calls and fake.calls[0][1] == url


def test_browser_policy_remains_browser_only() -> None:
    state = _url_state()
    url = "http://localhost:3000"
    envelope = _envelope("browser-local", "browser_extract_text", {"url": url})
    _register(state, envelope)
    policy = _LocalBlockingBrowserPolicy()
    with _access_mode("full_access"):
        decision = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="browser_extract_text",
            arguments=envelope.parsed_arguments,
            tool_call_envelope=envelope,
            raw_arguments=envelope.raw_arguments,
            browser_policy=policy,
        )
    assert decision.allowed is False
    assert decision.code == "blocked_localhost"
    assert decision.boundary == "browser_url"
    assert policy.calls == 1


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(
                WorkspaceManager(root).get_context("smoke", "url-execution-authority")
            )
            test_full_loop_fetch_failure_then_sandbox()
            test_url_state_does_not_block_granted_sandbox()
            test_read_only_still_blocks_sandbox()
            test_fetch_local_urls_keep_their_own_contract()
            test_browser_policy_remains_browser_only()
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_url_execution_authority_closure ok")


if __name__ == "__main__":
    main()
