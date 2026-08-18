"""Contract smoke for recoverable tool observations without runtime rerouting."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

_ENV = {
    "AGENT_ACCESS_MODE": "full_access",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
}
_PREVIOUS = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observation_compaction import compact_observation_for_model
from core.loop import AgentLoop
from core.state import PlanStep, TaskState
from core.task_profile import TaskProfile
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import (
    is_recoverable_observation,
    normalize_tool_result,
    observation_from_cache_snapshot,
    observation_to_cache_snapshot,
    observation_to_legacy_dict,
    observation_to_trace_dict,
)
from core.tool_outcome_resolution import resolve_tool_outcome
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.file_tools import read_file
import tools.file_tools as file_tools_module


def _envelope(call_id: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name="read_file",
        tool_name="read_file",
        canonical_name="read_file",
        executable_name="read_file",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "recoverable-smoke"},
    )


def _state() -> TaskState:
    profile = TaskProfile(
        task_type="simple",
        needs_web=False,
        has_url=False,
        has_search_engine_url=False,
        needs_code_edit=False,
        needs_validation=False,
        needs_git=False,
        user_intent_summary="recoverable selection",
        tool_required=True,
        side_effect_required=False,
        execution_mode="normal",
        structured_intent_type="file_read",
        structured_task_type="simple",
        structured_workflow_kind="simple",
    )
    state = TaskState.create(
        "recoverable-smoke",
        "simple",
        [PlanStep(index=1, name="read", instruction="read")],
        profile,
    )
    state.metadata.update(
        {
            "runtime_lane": "single_file_read",
            "required_capabilities": ["file_read"],
            "primary_capability": "file_read",
            "primary_tool": "read_file",
            "tool_plan": {
                "primary_capability": "file_read",
                "primary_tool": "read_file",
                "tool_priority": ["read_file", "read_document"],
            },
        }
    )
    return state


def main() -> None:
    legacy_next_field = "_".join(("next", "tool"))
    legacy_arguments_field = "_".join(("next", "arguments"))
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root).get_context("smoke", "recoverable-selection"))

            binary = root / "resource"
            binary.write_bytes(b"\xff\xfe\xfa")
            envelope = _envelope("call-binary", binary)
            raw = read_file(str(binary))
            assert raw["error_code"] == "tool_resource_incompatible"
            assert raw["recoverable"] is True
            assert raw["recovery_reason"] == "selected_text_reader_cannot_decode_resource"
            assert legacy_next_field not in raw and "recommended_tool" not in raw

            observation = normalize_tool_result(envelope, raw)
            assert is_recoverable_observation(observation)
            assert observation.error_code == "tool_resource_incompatible"
            assert observation.recoverable is True
            assert observation.recovery_reason == "selected_text_reader_cannot_decode_resource"

            legacy = observation_to_legacy_dict(observation)
            trace = observation_to_trace_dict(observation)
            compacted = compact_observation_for_model(observation)["model_visible_summary"]
            for projected in (legacy, trace, compacted):
                assert projected["recoverable"] is True
                assert projected["recovery_reason"] == "selected_text_reader_cannot_decode_resource"

            replay = observation_from_cache_snapshot(
                _envelope("call-replay", binary),
                observation_to_cache_snapshot(observation),
            )
            assert replay.recoverable is True and replay.recovery_reason == observation.recovery_reason

            state = _state()
            state.intent_runtime_context = SimpleNamespace(
                tool_plan=deepcopy(state.metadata["tool_plan"]),
            )
            before_plan = deepcopy(state.metadata["tool_plan"])
            before_context_plan = deepcopy(
                state.intent_runtime_context.tool_plan
            )
            loop = object.__new__(AgentLoop)
            loop._update_task_state(
                state,
                "read_file",
                {"path": str(binary)},
                observation_to_legacy_dict(observation),
            )
            assert state.metadata["tool_plan"] == before_plan
            assert state.intent_runtime_context.tool_plan == before_context_plan
            assert "recoverable_failure_agent_choice_pending" not in state.metadata
            assert "recoverable" + "_tool_selection_pending" not in state.metadata
            assert "continuation" + "_recovery_context" not in state.metadata
            outcome = resolve_tool_outcome(
                task_state=state,
                tool_name="read_file",
                arguments={"path": str(binary)},
                observation=legacy,
            )
            assert outcome.kind == "allow_continue"
            assert outcome.reason == "recoverable_tool_observation_returns_control_to_assistant"
            assert not hasattr(outcome, legacy_next_field)
            assert not hasattr(outcome, legacy_arguments_field)

            sensitive = root / ".env"
            sensitive.write_text("SECRET=not-readable", encoding="utf-8")
            for result in (
                read_file(str(root / "missing")),
                read_file(str(root)),
            ):
                assert result.get("recoverable") is False
                assert not result.get("recovery_reason")
            assert read_file(str(sensitive)).get("success") is True

            original_decision = file_tools_module._read_decision
            try:
                file_tools_module._read_decision = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("synthetic ordinary read failure")
                )
                ordinary = read_file(str(root / "ordinary"))
            finally:
                file_tools_module._read_decision = original_decision
            assert ordinary.get("recoverable") is False
            assert not ordinary.get("recovery_reason")
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_recoverable_tool_selection ok")


if __name__ == "__main__":
    main()
