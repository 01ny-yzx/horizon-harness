"""Focused Step 5 checks for exact Memory mutation and provenance."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop
from core.browser_policy import BrowserPolicy
from core.memory_mutation_policy import memory_mutation_context, validate_memory_provenance
from core.persistent_memory import PersistentMemory
from core.state import PlanStep, TaskState
from core.tool_risk_registry import get_tool_risk_metadata
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.registry import get_local_tool_registry
from tools.memory_tools import MEMORY_TOOLS, MEMORY_TOOL_SCHEMAS
import tools.memory_tools as memory_tools


def _observation(
    tool: str,
    call_id: str,
    *,
    success: bool = True,
    status: str = "success",
) -> dict[str, Any]:
    return {
        "observation_id": f"obs-{call_id}",
        "call_id": call_id,
        "provider_call_id": f"provider-{call_id}",
        "tool": tool,
        "success": success,
        "status": status,
    }


def _reference(memory: PersistentMemory, memory_type: str, query: str = "") -> dict[str, Any]:
    result = memory.search_memory_references(query, limit=100)
    assert result["success"] is True
    return next(
        item
        for item in result["data"]["references"]
        if item["memory_type"] == memory_type
    )


class _HistoryStore:
    def __init__(self) -> None:
        self.summaries: list[dict[str, Any]] = []

    def add_task_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        self.summaries.append(summary)
        return {"success": True}


def _history_state(observations: list[dict[str, Any]], status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(
        task_id="task-history",
        task_type="coding",
        user_goal="perform work",
        current_phase="done",
        metadata={"completion_observations": observations, "task_outcome_status": status},
        modified_files=[],
        research_queries=[],
        fetched_urls=[],
        memory_saved=False,
        saved_memory_types=[],
        is_finished=True,
    )


def test_provenance(memory: PersistentMemory) -> None:
    successful = _observation("read_file", "read-ok")
    failed = _observation("read_file", "read-failed", success=False, status="failed")
    blocked = _observation("sandbox_exec", "blocked", success=False, status="blocked")
    memory_observation = _observation("search_memory_references", "memory-read")
    state = SimpleNamespace(
        metadata={"completion_observations": [successful, failed, blocked, memory_observation]}
    )
    with memory_mutation_context(state):
        accepted = validate_memory_provenance("verified_observation", "read-ok")
        assert accepted.allowed and accepted.source == "verified_observation"
        assert validate_memory_provenance("verified_observation", "provider-read-ok").allowed
        assert validate_memory_provenance("verified_observation", "obs-read-ok").allowed
        for invalid in ("missing", "read-failed", "blocked", "memory-read"):
            assert not validate_memory_provenance("verified_observation", invalid).allowed
        assert not validate_memory_provenance("verified_observation", "").allowed
        assert validate_memory_provenance("user_explicit", "").allowed

    original = memory_tools._memory
    memory_tools._memory = lambda: memory
    try:
        with memory_mutation_context(state):
            assert memory_tools.remember_stable_fact(
                "OBSERVED_FACT",
                "Observed fact description",
                "verified_observation",
                "read-ok",
            )["success"]
            assert memory_tools.remember_project_summary(
                "/project/current",
                "OBSERVED_SUMMARY",
                "verified_observation",
                source_reference="provider-read-ok",
            )["success"]
            assert not memory_tools.remember_stable_fact(
                "INVALID_FACT",
                "Invalid description",
                "verified_observation",
                "missing",
            )["success"]
        assert memory_tools.remember_stable_fact(
            "USER_EXPLICIT_FACT",
            "Explicit description",
            "user_explicit",
        )["success"]
    finally:
        memory_tools._memory = original


def test_exact_mutations(memory: PersistentMemory, database_path: Path) -> None:
    assert memory.add_stable_fact("FACT_A", "Description A", source="user_explicit")["success"]
    assert memory.add_stable_fact("FACT_B PostgreSQL", "Description B", source="user_explicit")["success"]
    assert memory.add_stable_fact("FACT_C PostgreSQL", "Description C", source="user_explicit")["success"]

    upsert = memory.add_stable_fact(
        "FACT_A",
        "Description A revised",
        source="verified_observation",
        source_reference="read-ok",
    )
    assert upsert["success"] and upsert["data"]["updated"] is True
    fact_a = _reference(memory, "stable_fact", "FACT_A")
    old_reference_id = fact_a["reference_id"]
    old_body = memory.read_memory_reference(old_reference_id)["data"]["reference"]["content"]
    assert "Description A revised" in old_body and "verified_observation" in old_body

    updated = memory.update_stable_fact(
        old_reference_id,
        "FACT_A_UPDATED",
        "Updated fact description",
        source="user_explicit",
    )
    assert updated["success"] and updated["data"]["reference_id"] != old_reference_id
    new_reference_id = updated["data"]["reference_id"]
    assert not memory.read_memory_reference(old_reference_id)["success"]
    assert memory.read_memory_reference(new_reference_id)["success"]
    conflict = memory.update_stable_fact(
        new_reference_id,
        "FACT_B PostgreSQL",
        "Conflict",
        source="user_explicit",
    )
    assert not conflict["success"]

    fact_b = _reference(memory, "stable_fact", "FACT_B")
    deleted = memory.delete_memory_reference(fact_b["reference_id"])
    assert deleted["success"] and deleted["data"]["deleted"] == 1
    remaining = memory.search_memory_references("PostgreSQL", limit=20)["data"]["references"]
    assert [item["description"] for item in remaining] == ["Description C"]

    other_user = PersistentMemory(database_path=database_path, user_id="other", project_id="project-a")
    assert not other_user.delete_memory_reference(new_reference_id)["success"]
    assert not other_user.update_stable_fact(
        new_reference_id,
        "CROSS_SCOPE",
        "Cross scope",
        source="user_explicit",
    )["success"]

    assert memory.add_user_preference("answer_style", "short", source="user_explicit")["success"]
    assert memory.add_user_preference("answer_tone", "calm", source="user_explicit")["success"]
    assert memory.delete_user_preference("answer_style")["success"]
    assert "answer_tone" in memory.get_user_memory()["preferences"]

    assert memory.add_project_instruction("OLD", source="user_explicit")["success"]
    assert memory.add_project_instruction("KEEP", source="user_explicit")["success"]
    assert memory.update_project_instruction("OLD", "NEW")["success"]
    assert not memory.update_project_instruction("MISSING", "X")["success"]
    assert not memory.update_project_instruction("NEW", "KEEP")["success"]
    assert memory.delete_project_instruction("NEW")["success"]
    assert [item["content"] for item in memory.get_project_memory()["instructions"]] == ["KEEP"]

    assert memory.clear_memory_type("project_instructions")["success"]
    assert memory.get_project_memory()["instructions"] == []


def test_task_history_and_surface() -> None:
    execution_loop = object.__new__(AgentLoop)
    execution_loop.tools = get_local_tool_registry()
    execution_loop.mcp_registry = None
    execution_loop.browser_policy = BrowserPolicy()
    execution_loop._runtime_metrics = None
    attempted_state = TaskState.create(
        "remember",
        "simple",
        [PlanStep(index=1, name="memory", instruction="memory")],
    )
    failed_observation = execution_loop._execute_tool(
        attempted_state,
        "remember_stable_fact",
        '{"content":"missing required arguments"}',
    )
    assert failed_observation["success"] is False
    execution_loop._update_task_state(
        attempted_state,
        "remember_stable_fact",
        {"content": "missing required arguments"},
        failed_observation,
    )
    assert attempted_state.metadata["memory_tool_attempted"] is True
    assert attempted_state.metadata.get("memory_mutation_succeeded") is not True
    assert attempted_state.memory_saved is False

    memory_only_store = _HistoryStore()
    memory_only_loop = object.__new__(AgentLoop)
    memory_only_loop.persistent_memory = memory_only_store
    memory_only_state = _history_state([
        _observation("search_memory_references", "search"),
        _observation("delete_memory_reference", "delete"),
    ])
    memory_only_loop._save_task_history_after_task(memory_only_state, "done")
    assert memory_only_store.summaries == []

    for memory_success in (True, False):
        mixed_store = _HistoryStore()
        mixed_loop = object.__new__(AgentLoop)
        mixed_loop.persistent_memory = mixed_store
        mixed_state = _history_state([
            _observation("read_file", "read"),
            _observation(
                "remember_project_instruction",
                "memory",
                success=memory_success,
                status="success" if memory_success else "failed",
            ),
        ])
        mixed_loop._save_task_history_after_task(mixed_state, "mixed done")
        assert len(mixed_store.summaries) == 1
        assert mixed_store.summaries[0]["source"] == "task_record"

    summary_loop = object.__new__(AgentLoop)
    missing = _history_state([], status="")
    assert missing.is_finished is True
    assert summary_loop._build_task_summary(missing, "finished")["result"] == "incomplete_evidence"

    names = {item["function"]["name"] for item in MEMORY_TOOL_SCHEMAS}
    assert "forget_memory" not in names and "forget_memory" not in MEMORY_TOOLS
    assert "remember_task_history" not in names and "update_task_history" not in names
    for name in (
        "update_stable_fact",
        "update_project_instruction",
        "delete_memory_reference",
        "delete_user_preference",
        "delete_project_instruction",
    ):
        risk = get_tool_risk_metadata(name)
        assert risk is not None and risk.side_effect and risk.mutates_state
    assert "memory_delete_result" not in TaskState.__dataclass_fields__
    finish_source = inspect.getsource(AgentLoop._finish_with_trace)
    assert "memory_delete_result" not in finish_source
    assert 'task_outcome_status") == "completed"' in finish_source


def main() -> None:
    with TemporaryDirectory(prefix="horizon-step5-") as tmp:
        root = Path(tmp)
        database_path = root / "horizon.db"
        workspace = WorkspaceManager(
            root_dir=root / "workspaces",
            database_path=database_path,
        ).get_context("user-a", "project-a")
        set_current_workspace(workspace)
        memory = PersistentMemory(
            database_path=database_path,
            user_id="user-a",
            project_id="project-a",
        )
        test_provenance(memory)
        test_exact_mutations(memory, database_path)
        test_task_history_and_surface()
    print("smoke_memory_mutation_provenance ok")


if __name__ == "__main__":
    main()
