"""Verify Registry-owned WebSearch visibility and removed Web Tool closure."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.registry as registry_module
from core.intent_schema import TOOL_NAMES
from core.runtime_lane import RESEARCH_TOOLS
from core.status_tool_policy import STATUS_QUERY_TOOLS
from core.tool_risk_registry import get_tool_risk_metadata
from core.tool_selection_policy import candidate_tools_for_capability
from core.loop import AgentLoop
from core.memory import Memory
from providers.mock import MockProvider, assistant_message
from tools.registry import (
    get_local_tool_registry,
    get_local_tool_schemas,
    get_local_tool_specs,
)


REMOVED = {"get_search_status", "search_official_docs"}


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


class _AvailabilitySwitchingProvider(MockProvider):
    def __init__(
        self,
        *,
        state: dict[str, bool],
        next_search_available: bool,
        responses: list[Any],
    ) -> None:
        super().__init__(responses=responses)
        self.state = state
        self.next_search_available = next_search_available

    def chat(self, messages: Any, tools: Any, options: Any) -> Any:
        result = super().chat(messages, tools, options)
        if len(self.calls) == 1:
            self.state["search_available"] = self.next_search_available
        return result


def _schema_names() -> list[str]:
    return [
        str(schema.get("function", {}).get("name") or "")
        for schema in get_local_tool_schemas()
    ]


def _with_search_available(value: bool) -> list[str]:
    original = registry_module.get_web_search_provider_status
    registry_module.get_web_search_provider_status = lambda: SimpleNamespace(
        search_available=value
    )
    try:
        return _schema_names()
    finally:
        registry_module.get_web_search_provider_status = original


def test_registry_and_model_visibility() -> None:
    registered = set(get_local_tool_registry())
    assert {"web_search", "fetch_url"} <= registered
    assert not REMOVED.intersection(registered)

    available = _with_search_available(True)
    assert "web_search" in available
    assert "fetch_url" in available
    assert not REMOVED.intersection(available)

    unavailable = _with_search_available(False)
    assert "web_search" not in unavailable
    assert "fetch_url" in unavailable
    assert not REMOVED.intersection(unavailable)


def test_candidate_and_metadata_closure() -> None:
    assert candidate_tools_for_capability("web_search") == ["web_search"]
    specs = get_local_tool_specs()
    assert not REMOVED.intersection(specs)
    assert not REMOVED.intersection(TOOL_NAMES)
    assert not REMOVED.intersection(RESEARCH_TOOLS)
    assert not REMOVED.intersection(STATUS_QUERY_TOOLS)
    assert all(get_tool_risk_metadata(name) is None for name in REMOVED)


def _run_availability_transition(
    *,
    initial_available: bool,
    next_available: bool,
    initial_tool_name: str,
) -> tuple[MockProvider, list[str], dict[str, Any]]:
    state = {"search_available": initial_available}
    original = registry_module.get_web_search_provider_status
    registry_module.get_web_search_provider_status = lambda: SimpleNamespace(
        search_available=state["search_available"]
    )
    llm = _AvailabilitySwitchingProvider(
        state=state,
        next_search_available=next_available,
        responses=[
            assistant_message(
                "",
                [
                    _tool_call(
                        "initial-call",
                        initial_tool_name,
                        (
                            {"query": "surface transition"}
                            if initial_tool_name == "web_search"
                            else {"url": "https://8.8.8.8/"}
                        ),
                    )
                ],
            ),
            assistant_message("transition complete"),
        ],
    )
    executed: list[str] = []
    captured: dict[str, Any] = {}
    try:
        loop = AgentLoop(llm, Memory(), max_steps=3)

        def finish(
            self: AgentLoop,
            trace: Any,
            task_state: Any,
            answer: str,
        ) -> str:
            captured.update(trace=trace, task_state=task_state)
            return answer

        def fake_tool(**_: Any) -> dict[str, Any]:
            executed.append(initial_tool_name)
            return {
                "success": False,
                "status": "failed",
                "error": "simulated recoverable transition",
                "error_code": "simulated_transition",
                "recoverable": True,
                "recovery_reason": "retry_with_current_tool_surface",
                "data": {
                    "tool": initial_tool_name,
                    "recoverable": True,
                    "recovery_reason": "retry_with_current_tool_surface",
                },
            }

        loop.tools[initial_tool_name] = fake_tool
        loop._finish_with_trace = MethodType(finish, loop)
        assert loop.run(
            "verify dynamic web search surface",
            user_id="smoke",
            project_id=(
                "web-search-false-true"
                if initial_available is False
                else "web-search-true-false"
            ),
        ) == "transition complete"
    finally:
        registry_module.get_web_search_provider_status = original
    return llm, executed, captured


def _call_schema_names(llm: MockProvider, index: int) -> set[str]:
    return {
        str(schema.get("function", {}).get("name") or "")
        for schema in llm.calls[index]["tools"]
    }


def test_agent_loop_refreshes_false_to_true() -> None:
    llm, executed, _ = _run_availability_transition(
        initial_available=False,
        next_available=True,
        initial_tool_name="fetch_url",
    )
    assert executed == ["fetch_url"]
    assert "web_search" not in _call_schema_names(llm, 0)
    assert "fetch_url" in _call_schema_names(llm, 0)
    assert {"web_search", "fetch_url"} <= _call_schema_names(llm, 1)


def test_agent_loop_refreshes_true_to_false() -> None:
    llm, executed, _ = _run_availability_transition(
        initial_available=True,
        next_available=False,
        initial_tool_name="web_search",
    )
    assert executed == ["web_search"]
    assert {"web_search", "fetch_url"} <= _call_schema_names(llm, 0)
    assert "web_search" not in _call_schema_names(llm, 1)
    assert "fetch_url" in _call_schema_names(llm, 1)


def main() -> None:
    test_registry_and_model_visibility()
    test_candidate_and_metadata_closure()
    test_agent_loop_refreshes_false_to_true()
    test_agent_loop_refreshes_true_to_false()
    print("smoke_web_search_tool_surface_reset ok")


if __name__ == "__main__":
    main()
