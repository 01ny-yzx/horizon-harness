from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.final_responder as final_responder
from core.final_responder import (
    build_final_responder_pack_from_snapshot,
    build_final_responder_response_contract,
    validate_final_responder_message,
)
from core.final_observation_context import build_final_observation_context
from core.finalization_context_snapshot import (
    FINALIZATION_CONTEXT_SNAPSHOT_VERSION,
    build_finalization_context_snapshot,
)
from core.finalization_outlet import resolve_finalization_outlet
from core.tool_outcome_resolution import ToolOutcomeResolution


class DummyTaskState:
    task_type = "research"
    user_goal = "ignored by responder validation"
    metadata = {"runtime_lane": "research"}
    tool_failures: list[dict[str, object]] = []
    file_output_completed = False


def _outcome(kind: str, tool: str, observation: dict[str, object], reason: str = "task_complete_after_tool_success") -> ToolOutcomeResolution:
    return ToolOutcomeResolution(
        kind,  # type: ignore[arg-type]
        reason,
        tool=tool,
        status=str(observation.get("status") or ""),
        policy_code=str(observation.get("policy_code") or observation.get("error_code") or ""),
        metadata={"observation": observation},
    )


def _snapshot(outcome: ToolOutcomeResolution):
    return build_finalization_context_snapshot(
        user_request="终局结果",
        task_state=DummyTaskState(),
        outcome=outcome,
        finalization_mode="terminal_responder",
        finalization_reason=outcome.reason,
    )


def test_pack_contract_exists() -> None:
    outcome = _outcome(
        "terminal_success",
        "get_usage_status",
        {
            "tool": "get_usage_status",
            "success": True,
            "status": "success",
            "data": {
                "active_provider": "tavily",
                "configured_provider": "tavily",
                "search_available": True,
                "fetch_url_available": True,
                "reason": "Tavily search is available.",
            },
        },
        reason="status_tool_success",
    )
    pack = build_final_responder_pack_from_snapshot(_snapshot(outcome))
    assert pack.metadata["finalization_snapshot_summary"]["result_count"] == 1
    assert pack.metadata["response_contract"]
    assert pack.metadata["response_contract_version"] == "terminal_responder_contract_v1"
    assert pack.metadata["tools_disabled"] is True
    assert pack.metadata["finalization_mode"] == "terminal_responder"
    assert pack.metadata["context_schema"] == FINALIZATION_CONTEXT_SNAPSHOT_VERSION
    assert pack.tool_schema_chars == 0
    assert "Final responder response contract" in json.dumps(
        pack.messages,
        ensure_ascii=False,
    )


def test_sandbox_exec_success_context_and_contract() -> None:
    outcome = _outcome(
        "terminal_success",
        "sandbox_exec",
        {"tool": "sandbox_exec", "success": True, "status": "success", "data": {"command": "python -c 'print(123)'", "exit_code": 0, "stdout": "123\n"}},
    )
    context = build_final_observation_context(DummyTaskState(), outcome)
    summary = context[0]["data_summary"]
    contract_text = json.dumps(build_final_responder_response_contract(), ensure_ascii=False)
    assert summary["exit_code"] == 0
    assert "123" in summary["stdout_preview"]
    assert "stdout" in contract_text
    assert "stderr" in contract_text
    assert "exit_code" in contract_text


def test_sandbox_exec_failure_context_and_contract() -> None:
    outcome = _outcome(
        "terminal_failure",
        "sandbox_exec",
        {
            "tool": "sandbox_exec",
            "success": False,
            "status": "failed",
            "error": "command not found",
            "error_code": "command_not_found",
            "data": {"command": "missing-command", "exit_code": 127, "stderr": "command not found"},
        },
    )
    context = build_final_observation_context(DummyTaskState(), outcome)
    summary = context[0]["data_summary"]
    contract_text = json.dumps(build_final_responder_response_contract(), ensure_ascii=False)
    assert summary["exit_code"] == 127
    assert "command not found" in summary["stderr_preview"]
    assert context[0]["error"] == "command not found"
    assert "Do not say you will call, read, fetch, run, search, retry, or inspect more tools" in contract_text
    assert "non-zero" in contract_text
    assert "command failed" in contract_text


def test_partial_outcome_contract() -> None:
    contract_text = json.dumps(build_final_responder_response_contract(), ensure_ascii=False)
    assert "partially completed" in contract_text
    assert "blocked tool did not complete" in contract_text
    assert "Do not hide successful results" in contract_text


def test_validation_accepts_normal_answer() -> None:
    validation = validate_final_responder_message(SimpleNamespace(content="验证已通过，输出为 123。", tool_calls=[]))
    assert validation.accepted is True
    assert validation.content
    assert validation.reject_reason == ""


def test_validation_rejects_empty_content() -> None:
    validation = validate_final_responder_message(SimpleNamespace(content="", tool_calls=[]))
    assert validation.accepted is False
    assert validation.reject_reason == "empty_content"


def test_validation_rejects_tool_calls() -> None:
    validation = validate_final_responder_message(SimpleNamespace(content="x", tool_calls=[{"function": {"name": "read_file"}}]))
    assert validation.accepted is False
    assert validation.reject_reason == "assistant_requested_tool_call"


def test_validation_rejects_future_tool_action_commitment() -> None:
    samples = [
        "我将继续读取文件。",
        "我会再执行命令。",
        "我接下来会重新运行工具。",
        "我需要调用工具。",
        "我会继续搜索。",
    ]
    for sample in samples:
        validation = validate_final_responder_message(SimpleNamespace(content=sample, tool_calls=[]))
        assert validation.accepted is False
        assert validation.reject_reason == "assistant_committed_future_tool_action"


def test_policy_blocked_uses_llm_responder() -> None:
    outcome = _outcome(
        "terminal_policy_blocked",
        "fetch_url",
        {
            "tool": "fetch_url",
            "success": False,
            "status": "blocked",
            "error": "only http and https URLs are allowed.",
            "error_code": "unsupported_scheme",
        },
        reason="policy_blocked",
    )
    outlet = resolve_finalization_outlet(DummyTaskState(), outcome)
    assert outlet.kind == "terminal_responder"
    assert outlet.reason == "terminal_policy_blocked"
    assert outlet.policy_code == "unsupported_scheme"


def test_validation_rejects_raw_tool_text() -> None:
    samples = [
        '<tool_call>{"name":"read_file","arguments":{"path":"a.txt"}}</tool_call>',
        '{"function":{"name":"read_file","arguments":{"path":"a.txt"}}}',
    ]
    for sample in samples:
        validation = validate_final_responder_message(SimpleNamespace(content=sample, tool_calls=[]))
        assert validation.accepted is False
        assert validation.reject_reason == "raw_tool_text_in_final_responder"


def test_tools_disabled_metadata_and_no_tool_schema_exposure() -> None:
    outcome = _outcome("terminal_success", "sandbox_exec", {"tool": "sandbox_exec", "success": True, "status": "success", "data": {"exit_code": 0, "stdout": "ok"}})
    pack = build_final_responder_pack_from_snapshot(_snapshot(outcome))
    rendered = json.dumps(pack.messages, ensure_ascii=False)
    assert pack.metadata["tools_disabled"] is True
    assert pack.tool_schema_chars == 0
    assert '"tools"' not in rendered
    assert "available_tool_names" not in rendered


def test_no_user_text_keyword_routing() -> None:
    source = inspect.getsource(final_responder.validate_final_responder_message)
    assert "user_input" not in source
    assert "user_goal" not in source
    assert "re.search" not in source
    module_source = inspect.getsource(final_responder)
    assert "re.search" not in module_source
    assert "same language as the current user request" in module_source


def main() -> None:
    test_pack_contract_exists()
    test_sandbox_exec_success_context_and_contract()
    test_sandbox_exec_failure_context_and_contract()
    test_partial_outcome_contract()
    test_validation_accepts_normal_answer()
    test_validation_rejects_empty_content()
    test_validation_rejects_tool_calls()
    test_validation_rejects_future_tool_action_commitment()
    test_policy_blocked_uses_llm_responder()
    test_validation_rejects_raw_tool_text()
    test_tools_disabled_metadata_and_no_tool_schema_exposure()
    test_no_user_text_keyword_routing()
    print("smoke_llm_final_responder_complete ok")


if __name__ == "__main__":
    main()
