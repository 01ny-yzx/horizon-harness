"""Focused smoke for the neutral Agent prose and terminal finalization architecture."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_prose_validation import validate_agent_prose_candidate
from core.loop import AgentLoop
from core.memory import Memory
from core.state import TaskState
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message
from scripts.smoke_document_read_load_capability_separation import (
    _call,
    _make_xlsx,
    _message,
    _run_loop,
    document_tools,
    real_read_document,
)


def _event_types(captured: dict[str, Any]) -> list[str]:
    return [event.event_type for event in captured["trace"].events]


def _forbidden_tokens() -> tuple[str, ...]:
    return (
        "final_answer" + "_v2",
        "build_final_answer" + "_v2",
        "is_final_answer_candidate" + "_v2",
        "format_memory_delete_final" + "_v2",
        "Legacy" + "FinalAnswer",
        "legacy_final_answer" + "_fallback",
        "legacy_" + "fallback",
        "legacy_" + "runtime",
        "FinalAnswer" + " v2",
    )


def test_filesystem_and_source_cleanup() -> None:
    old_directory = ROOT / "core" / ("final_answer" + "_v2")
    old_entry = ROOT / "core" / ("final_answer" + ".py")
    old_smoke = ROOT / "scripts" / (
        "smoke_"
        + "legacy_"
        + "final_answer_"
        + "fallback_boundary.py"
    )
    assert not old_directory.exists()
    assert not old_entry.exists()
    assert not old_smoke.exists()

    scan_roots = (
        "core",
        "tools",
        "providers",
        "workflows",
        "scripts",
        "evals",
        "api",
    )
    tokens = _forbidden_tokens()
    matches: list[str] = []
    for relative in scan_roots:
        directory = ROOT / relative
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in tokens:
                if token in text:
                    matches.append(f"{path.relative_to(ROOT)}:{token}")
    for relative in ("main.py",):
        path = ROOT / relative
        if path.exists():
            text = path.read_text(encoding="utf-8")
            for token in tokens:
                if token in text:
                    matches.append(f"{relative}:{token}")
    assert not matches, matches


def test_minimal_agent_prose_validation() -> None:
    accepted = (
        "已创建 chunks，并已保存 `/tmp/source.xlsx`。",
        "The document was created and written.",
        '{"status":"created","path":"/tmp/output.json"}',
        "# 完成\n\n| key | value |\n|---|---|\n| status | saved |",
    )
    for content in accepted:
        result = validate_agent_prose_candidate(content)
        assert result.accepted is True
        assert result.content == content
    assert (
        validate_agent_prose_candidate("").reject_reason
        == "empty_content"
    )
    assert (
        validate_agent_prose_candidate(
            '<tool_call name="read_file">{"path":"a.txt"}</tool_call>'
        ).reject_reason
        == "raw_tool_text_in_agent_prose"
    )


def test_read_document_direct_prose(root: Path) -> None:
    document = root / "read.xlsx"
    _make_xlsx(document)
    prose = "已读取工作簿，并根据实际结果完成总结。"
    answer, llm, captured, executed = _run_loop(
        [
            _message(
                "",
                [_call("read-document", "read_document", {"path": str(document)})],
            ),
            _message(prose),
        ],
        request="读取这个文档并总结。",
        tools={"read_document": real_read_document},
        project_id="finalization-read",
    )
    assert answer == prose
    assert executed == ["read_document"]
    assert len(llm.calls) == 2
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    events = _event_types(captured)
    assert "candidate_rejected" not in events
    assert "terminal_responder_llm" not in events
    assert "emergency_finalization_fallback" not in events
    assert "max_steps_reached" not in events


def test_document_load_prose_is_not_misclassified(root: Path) -> None:
    document = root / "table.xlsx"
    _make_xlsx(document)
    result_box: dict[str, Any] = {}

    def load_document(path: str, **kwargs: Any) -> dict[str, Any]:
        result = document_tools.load_document(path, **kwargs)
        result_box["result"] = result
        return result

    prose = (
        f"已将 `{document}` 导入当前 workspace 的本地知识库，"
        "并成功创建文档分块。"
    )
    answer, llm, captured, executed = _run_loop(
        [
            _message(
                "",
                [
                    _call(
                        "load-document",
                        "load_document",
                        {"path": str(document), "create_chunks": True},
                    )
                ],
            ),
            _message(prose),
        ],
        request="将这个文档导入当前 workspace 的本地知识库。",
        tools={"load_document": load_document},
        project_id="finalization-load",
    )
    assert answer == prose
    assert executed == ["load_document"]
    assert len(llm.calls) == 2
    assert result_box["result"]["success"] is True
    assert captured["state"].metadata["observed_capabilities"] == [
        "document_load"
    ]
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    old_claim_key = "file_write" + "_claim_without_observation"
    assert old_claim_key not in captured["state"].metadata
    events = _event_types(captured)
    assert "candidate_rejected" not in events
    assert "terminal_responder_llm" not in events
    assert "emergency_finalization_fallback" not in events
    assert "max_steps_reached" not in events


def test_second_raw_agent_prose_reaches_terminal_boundary(root: Path) -> None:
    document = root / "raw.xlsx"
    _make_xlsx(document)
    raw = '<tool_call name="read_document">{"path":"raw.xlsx"}</tool_call>'
    terminal_prose = "模型连续返回了无效工具协议文本，任务已停止。"
    answer, llm, captured, executed = _run_loop(
        [
            _message(
                "",
                [_call("raw-read", "read_document", {"path": str(document)})],
            ),
            _message(raw),
            _message(raw),
            _message(terminal_prose),
        ],
        request="读取该文档。",
        tools={"read_document": real_read_document},
        project_id="raw-prose-boundary",
        max_steps=5,
    )
    assert answer == terminal_prose
    assert executed == ["read_document"]
    assert len(llm.calls) == 4
    events = _event_types(captured)
    assert events.count("candidate_rejected") == 2
    assert "terminal_responder_llm" in events
    assert "max_steps_reached" not in events


def test_terminal_emergency_path_is_tools_disabled() -> None:
    outcome = ToolOutcomeResolution(
        "terminal_failure",
        "execution_failed",
        tool="sandbox_exec",
        status="failed",
        metadata={
            "observation": {
                "call_id": "internal-call",
                "tool": "sandbox_exec",
                "success": False,
                "status": "failed",
                "error_code": "command_failed",
                "data": {"exit_code": 9, "stderr": "failed"},
            }
        },
    )
    state = TaskState.create("运行命令", "simple", [], None)
    llm = MockProvider(responses=[assistant_message("")])
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = llm
    loop.memory = Memory()
    loop._runtime_metrics = None
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    answer = loop._build_final_answer_from_terminal_outcome(
        state,
        outcome,
        trace,
        1,
    )
    assert len(llm.calls) == 1 and llm.calls[0]["tools"] == []
    assert "command_failed" in answer
    assert "9" in answer
    assert "internal-call" not in answer
    events = [event.event_type for event in trace.events]
    assert "terminal_responder_validation" in events
    assert "emergency_finalization_fallback" in events


def main() -> None:
    old_cwd = Path.cwd()
    old_workspace_root = os.environ.get("WORKSPACE_ROOT")
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            os.environ["WORKSPACE_ROOT"] = str(root / "workspace_store")
            test_filesystem_and_source_cleanup()
            test_minimal_agent_prose_validation()
            test_read_document_direct_prose(root)
            test_document_load_prose_is_not_misclassified(root)
            test_second_raw_agent_prose_reaches_terminal_boundary(root)
            test_terminal_emergency_path_is_tools_disabled()
    finally:
        os.chdir(old_cwd)
        if old_workspace_root is None:
            os.environ.pop("WORKSPACE_ROOT", None)
        else:
            os.environ["WORKSPACE_ROOT"] = old_workspace_root
    print("smoke_finalization_architecture_cleanup ok")


if __name__ == "__main__":
    main()
