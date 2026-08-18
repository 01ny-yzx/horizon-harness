from __future__ import annotations

import inspect
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.finalization_outlet import resolve_finalization_outlet
from core.tool_outcome_resolution import ToolOutcomeResolution


class DummyTaskState:
    user_goal = ""

    def __init__(self, observations: list[dict[str, object]] | None = None) -> None:
        self.metadata: dict[str, object] = {
            "completion_observations": list(observations or []),
        }


def test_terminal_policy_blocked_outlet() -> None:
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="fetch_url",
        status="blocked",
        policy_code="unsupported_scheme",
        metadata={
            "observation": {
                "success": False,
                "status": "blocked",
                "kind": "internal",
                "tool": "fetch_url",
                "error": "only http and https URLs are allowed.",
                "error_code": "unsupported_scheme",
            }
        },
    )
    outlet = resolve_finalization_outlet(DummyTaskState(), outcome)
    assert outlet.kind == "terminal_responder"
    assert outlet.reason == "terminal_policy_blocked"
    assert outlet.policy_code == "unsupported_scheme"


def test_unknown_policy_preserves_structured_code_for_llm_final() -> None:
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="example_tool",
        status="blocked",
        policy_code="unknown_boundary_code",
        metadata={"observation": {"error": "low-level English exception", "error_code": "unknown_boundary_code"}},
    )
    outlet = resolve_finalization_outlet(DummyTaskState(), outcome)
    assert outlet.kind == "terminal_responder"
    assert outlet.reason == "terminal_policy_blocked"
    assert outlet.policy_code == "unknown_boundary_code"


def test_terminal_success_outlet() -> None:
    outcome = ToolOutcomeResolution(
        "terminal_success",
        "status_tool_success",
        tool="get_usage_status",
        status="success",
        metadata={
            "observation": {
                "success": True,
                "status": "success",
                "kind": "internal",
                "tool": "get_usage_status",
                "data": {"status": "available"},
            }
        },
    )
    outlet = resolve_finalization_outlet(DummyTaskState(), outcome)
    assert outlet.kind == "terminal_responder"


def test_partial_success_before_policy_block_uses_llm_final() -> None:
    success = {
        "call_id": "call-read",
        "tool": "read_file",
        "success": True,
        "status": "success",
        "data": {"path": "a.txt", "content": "visible result"},
    }
    blocked = {
        "call_id": "call-command",
        "tool": "sandbox_exec",
        "success": False,
        "status": "blocked",
        "error_code": "dangerous_command",
    }
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="sandbox_exec",
        status="blocked",
        policy_code="dangerous_command",
        metadata={"observation": blocked},
    )
    outlet = resolve_finalization_outlet(DummyTaskState([success, blocked]), outcome)
    assert outlet.kind == "terminal_responder"
    assert outlet.reason == "partial_outcome_with_policy_block"
    assert outlet.policy_code == "dangerous_command"


def test_no_user_text_keyword_routing() -> None:
    source = inspect.getsource(resolve_finalization_outlet)
    assert "user_goal" not in source
    assert "user_input" not in source
    assert "re.search" not in source
    assert "状态" not in source
    assert "调用" not in source
    assert "错误" not in source


def main() -> None:
    test_terminal_policy_blocked_outlet()
    test_unknown_policy_preserves_structured_code_for_llm_final()
    test_terminal_success_outlet()
    test_partial_success_before_policy_block_uses_llm_final()
    test_no_user_text_keyword_routing()
    print("smoke_finalization_outlet ok")


if __name__ == "__main__":
    main()
