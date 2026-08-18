"""Smoke checks for structured failure disposition and natural failure completion."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

from core.loop import AgentLoop
from core.memory import Memory
from core.tool_outcome_resolution import (
    resolve_failure_disposition,
    resolve_tool_outcome,
)
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider, assistant_message
from tools.file_tools import read_document as real_read_document
from tools.file_tools import read_file as real_read_file


def _state(path: Path | None = None) -> SimpleNamespace:
    profile = SimpleNamespace(document_paths=[str(path)] if path is not None else [])
    return SimpleNamespace(
        task_profile=profile,
        metadata={"tool_plan": {}},
        tool_failures=[],
    )


def _failure(
    error_code: str,
    path: Path | None = None,
    *,
    recoverable: bool = False,
    policy_code: str = "",
    exit_code: int | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "status": "blocked" if policy_code else "failed",
        "error_code": error_code,
        "policy_code": policy_code,
        "recoverable": recoverable,
        "recovery_reason": (
            "selected_text_reader_cannot_decode_resource" if recoverable else ""
        ),
        "data": {
            "path": str(path) if path is not None else "",
            "error_code": error_code,
            "exit_code": exit_code,
        },
        "metadata": {"path": str(path) if path is not None else ""},
        "exit_code": exit_code,
        "kind": "execution" if exit_code is not None else "read",
        "tool": "sandbox_exec" if exit_code is not None else "",
    }


def _assert_disposition(
    *,
    state: Any,
    tool_name: str,
    arguments: dict[str, Any],
    observation: dict[str, Any],
    disposition: str,
    kind: str,
) -> None:
    decision = resolve_failure_disposition(
        task_state=state,
        tool_name=tool_name,
        arguments=arguments,
        observation=observation,
    )
    outcome = resolve_tool_outcome(
        task_state=state,
        tool_name=tool_name,
        arguments=arguments,
        observation=observation,
    )
    assert decision.disposition == disposition, decision
    assert outcome.failure_disposition == disposition, outcome
    assert outcome.kind == kind, outcome


def test_failure_disposition_matrix(root: Path) -> None:
    authoritative = (root / "authoritative.xlsx").resolve()
    other = (root / "guessed.xlsx").resolve()
    state = _state(authoritative)

    _assert_disposition(
        state=state,
        tool_name="read_file",
        arguments={"path": str(authoritative)},
        observation=_failure(
            "tool_resource_incompatible",
            authoritative,
            recoverable=True,
        ),
        disposition="recoverable",
        kind="allow_continue",
    )
    _assert_disposition(
        state=state,
        tool_name="sandbox_exec",
        arguments={"command": "false"},
        observation=_failure("command_failed", exit_code=1),
        disposition="ordinary_failure",
        kind="allow_continue",
    )
    _assert_disposition(
        state=state,
        tool_name="read_file",
        arguments={"path": str(authoritative)},
        observation=_failure("file_not_found", authoritative),
        disposition="ordinary_failure",
        kind="allow_continue",
    )
    _assert_disposition(
        state=state,
        tool_name="read_file",
        arguments={"path": str(other)},
        observation=_failure("file_not_found", other),
        disposition="ordinary_failure",
        kind="allow_continue",
    )
    for error_code in (
        "document_xlsx_invalid_or_corrupt",
        "document_legacy_format_unsupported",
    ):
        _assert_disposition(
            state=state,
            tool_name="read_document",
            arguments={"path": str(authoritative)},
            observation=_failure(error_code, authoritative),
            disposition="ordinary_failure",
            kind="allow_continue",
        )
    _assert_disposition(
        state=state,
        tool_name="read_document",
        arguments={"path": str(authoritative)},
        observation=_failure("document_read_failed", authoritative),
        disposition="ordinary_failure",
        kind="allow_continue",
    )
    _assert_disposition(
        state=state,
        tool_name="read_file",
        arguments={"path": str(authoritative)},
        observation=_failure(
            "path_outside_allowed_roots",
            authoritative,
            policy_code="path_outside_allowed_roots",
        ),
        disposition="policy_blocked",
        kind="terminal_policy_blocked",
    )


def _call(call_id: str, name: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps({"path": str(path)}, ensure_ascii=False),
        ),
    )


def _run_ordinary_failure_case(
    *,
    path: Path,
    tool_name: str,
    final_text: str,
) -> tuple[dict[str, Any], MockProvider, list[str], str]:
    llm = MockProvider(
        responses=[
            assistant_message("", [_call(f"call-{tool_name}", tool_name, path)]),
            assistant_message(final_text),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    captured: dict[str, Any] = {}
    executed: list[str] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    def read_file(path: str, **kwargs: Any) -> dict[str, Any]:
        executed.append("read_file")
        return real_read_file(path, **kwargs)

    def read_document(path: str, **kwargs: Any) -> dict[str, Any]:
        executed.append("read_document")
        return real_read_document(path, **kwargs)

    loop._finish_with_trace = MethodType(finish, loop)
    loop.tools["read_file"] = read_file
    loop.tools["read_document"] = read_document
    original_execute = loop._execute_tool_envelope

    def execute_with_authoritative_profile(
        self: AgentLoop,
        task_state: Any,
        envelope: Any,
        **kwargs: Any,
    ) -> Any:
        task_state.task_profile = replace(
            task_state.task_profile,
            document_paths=[str(path)],
        )
        return original_execute(task_state, envelope, **kwargs)

    loop._execute_tool_envelope = MethodType(execute_with_authoritative_profile, loop)
    answer = loop.run(f"处理明确目标文件：{path}")
    return captured, llm, executed, answer


def _assert_ordinary_failure_full_loop(
    *,
    captured: dict[str, Any],
    llm: MockProvider,
    executed: list[str],
    expected_tool: str,
    expected_error_code: str,
) -> None:
    assert executed == [expected_tool]
    stages = [str(call["options"].stage) for call in llm.calls]
    assert stages == ["initial_agent_turn", "agent_continuation"], stages
    events = captured["trace"].events
    dispositions = [
        event.data
        for event in events
        if event.event_type == "tool_failure_disposition"
    ]
    assert len(dispositions) == 1
    assert dispositions[0]["disposition"] == "ordinary_failure"
    assert dispositions[0]["outcome_kind"] == "allow_continue"
    assert dispositions[0]["error_code"] == expected_error_code
    assert dispositions[0]["authoritative_target"] is True
    assert not any(event.event_type == "max_steps_reached" for event in events)
    assert not any(
        event.event_type in {
            "emergency_finalization_fallback",
            "emergency_finalization_started",
        }
        for event in events
    )


def test_ordinary_failure_full_loop_cases(root: Path) -> None:
    missing = (root / "missing.txt").resolve()
    captured, llm, executed, answer = _run_ordinary_failure_case(
        path=missing,
        tool_name="read_file",
        final_text="指定文件不存在，无法读取。",
    )
    assert answer == "指定文件不存在，无法读取。"
    _assert_ordinary_failure_full_loop(
        captured=captured,
        llm=llm,
        executed=executed,
        expected_tool="read_file",
        expected_error_code="file_not_found",
    )
    assert not {"list_files", "find_files", "sandbox_exec"} & set(executed)

    corrupt = (root / "corrupt.xlsx").resolve()
    corrupt.write_bytes(b"not-a-valid-xlsx")
    captured, llm, executed, answer = _run_ordinary_failure_case(
        path=corrupt,
        tool_name="read_document",
        final_text="这个 XLSX 已损坏，无法读取。",
    )
    assert "XLSX" in answer
    _assert_ordinary_failure_full_loop(
        captured=captured,
        llm=llm,
        executed=executed,
        expected_tool="read_document",
        expected_error_code="document_xlsx_invalid_or_corrupt",
    )

    legacy = (root / "legacy.xls").resolve()
    legacy.write_bytes(b"legacy")
    captured, llm, executed, answer = _run_ordinary_failure_case(
        path=legacy,
        tool_name="read_document",
        final_text="暂不支持 .xls，请先转换为 .xlsx。",
    )
    assert ".xlsx" in answer and ".excelx" not in answer
    _assert_ordinary_failure_full_loop(
        captured=captured,
        llm=llm,
        executed=executed,
        expected_tool="read_document",
        expected_error_code="document_legacy_format_unsupported",
    )


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(
                WorkspaceManager(root).get_context(
                    "smoke",
                    "failure-recoverability-finalization",
                )
            )
            test_failure_disposition_matrix(root)
            test_ordinary_failure_full_loop_cases(root)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_failure_recoverability_finalization ok")


if __name__ == "__main__":
    main()
