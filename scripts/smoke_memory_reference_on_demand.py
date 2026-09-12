"""Focused smoke for request-time memory discovery and on-demand reading."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import patch

_ENV_OVERRIDES = {
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "MCP_ENABLED": "false",
    "EMBEDDING_ENABLED": "false",
    "AGENT_ACCESS_MODE": "full_access",
}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}
os.environ.update(_ENV_OVERRIDES)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.loop as loop_module
from core.loop import AgentLoop
from core.memory import Memory
from core.memory_reference_guidance import resolve_memory_reference_guidance
from core.path_grounding import build_path_context
from core.persistent_memory import PersistentMemory
from core.request_guidance import resolve_request_guidance
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider, assistant_message
from tools import memory_tools
from tools.memory_tools import MEMORY_TOOL_SCHEMAS


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _render(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def _memory(database_path: Path, user: str, project: str) -> PersistentMemory:
    return PersistentMemory(database_path=database_path, user_id=user, project_id=project)


def _finish_without_persistence(loop: AgentLoop) -> None:
    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        return answer

    loop._finish_with_trace = MethodType(finish, loop)


def _seed_catalog(memory: PersistentMemory, *, prefix: str = "CATALOG") -> None:
    with patch.object(memory_tools, "_memory", return_value=memory):
        assert memory_tools.remember_stable_fact(
            f"{prefix}_STABLE_BODY",
            f"{prefix}_STABLE_DESC",
            provenance_kind="user_explicit",
        ).get("success")
    assert memory.add_project_summary(
        f"/{prefix.lower()}",
        f"{prefix}_PROJECT_BODY",
        ["Python"],
        "active",
        source_reference="source:project",
    ).get("success")
    assert memory.add_task_summary(
        {
            "task_id": f"history-{prefix.lower()}",
            "task_type": "simple",
            "goal": f"{prefix}_TASK_GOAL",
            "result": "completed",
            "summary": f"{prefix}_TASK_BODY",
            "source": "agent",
        }
    ).get("success")


def test_catalog_and_search(database_path: Path, root: Path) -> None:
    memory = _memory(database_path, "catalog-user", "catalog-project")
    _seed_catalog(memory)
    assert memory.add_stable_fact("EMPTY_DESCRIPTION_BODY", "   ")["success"] is False
    assert memory.add_stable_fact("SAFE_BODY", "api_key=do-not-store")["success"] is False
    assert memory.add_user_preference("style", "GUIDANCE_PREF_ONLY").get("success")
    assert memory.add_project_instruction("GUIDANCE_PROJECT_ONLY").get("success")
    for index in range(30):
        assert memory.add_stable_fact(
            f"BULK_REFERENCE_{index:02d}",
            f"Bulk reference {index:02d}",
        ).get("success")
    for suffix in ("A", "B", "C"):
        assert memory.add_stable_fact(
            f"DISTINCT_FACT_{suffix}_BODY",
            f"DISTINCT_FACT_{suffix}_DESC",
        ).get("success")
    assert memory.add_project_summary(
        "/catalog-two",
        "CATALOG_SECOND_PROJECT_BODY",
        ["Go"],
        "maintenance",
    ).get("success")
    assert memory.add_task_summary(
        {
            "task_id": "history-catalog-two",
            "task_type": "debug",
            "goal": "CATALOG_SECOND_TASK_GOAL",
            "result": "fixed",
            "summary": "CATALOG_SECOND_TASK_BODY",
        }
    ).get("success")
    assert memory.add_task_summary(
        {
            "task_id": "hidden-history",
            "summary": "HIDDEN_TASK_BODY",
            "hidden_from_memory_prompt": True,
        }
    ).get("success")

    guidance = resolve_memory_reference_guidance(persistent_memory=memory)
    rendered = _render(list(guidance.system_messages))
    assert guidance.available and guidance.load_success
    assert guidance.available_types == ("stable_fact", "project_summary", "task_history")
    assert guidance.counts == {"stable_fact": 34, "project_summary": 2, "task_history": 2}
    for name, scope in (("stable_fact", "user"), ("project_summary", "project"), ("task_history", "project")):
        assert f"<name>{name}</name>" in rendered
        assert f"<scope>{scope}</scope>" in rendered
    for body in (
        "CATALOG_STABLE_BODY",
        "CATALOG_PROJECT_BODY",
        "CATALOG_TASK_BODY",
        "CATALOG_SECOND_PROJECT_BODY",
        "CATALOG_SECOND_TASK_BODY",
        "BULK_REFERENCE_00",
        "BULK_REFERENCE_23",
        "HIDDEN_TASK_BODY",
        "GUIDANCE_PREF_ONLY",
        "GUIDANCE_PROJECT_ONLY",
    ):
        assert body not in rendered
    assert "reference_id" not in rendered and "limit=24" not in rendered

    search = memory.search_memory_references("CATALOG", limit=100)
    references = search["data"]["references"]
    assert {item["memory_type"] for item in references} == {
        "stable_fact",
        "project_summary",
        "task_history",
    }
    common = {"reference_id", "memory_type", "scope", "source", "updated_at", "matched_terms"}
    assert all(common.issubset(item) and "content" not in item for item in references)
    stable_refs = [item for item in references if item["memory_type"] == "stable_fact"]
    project_refs = [item for item in references if item["memory_type"] == "project_summary"]
    task_refs = [item for item in references if item["memory_type"] == "task_history"]
    assert stable_refs[0]["description"] == "CATALOG_STABLE_DESC"
    assert all("description" not in item for item in project_refs + task_refs)
    assert {(item["project_path"], item["status"], tuple(item["tech_stack"])) for item in project_refs} == {
        ("/catalog", "active", ("Python",)),
        ("/catalog-two", "maintenance", ("Go",)),
    }
    assert {(item["task_id"], item["task_type"], item["goal"], item["result"]) for item in task_refs} == {
        ("history-catalog", "simple", "CATALOG_TASK_GOAL", "completed"),
        ("history-catalog-two", "debug", "CATALOG_SECOND_TASK_GOAL", "fixed"),
    }
    assert all("summary" not in item and "modified_files" not in item for item in project_refs + task_refs)
    search_text = json.dumps(references, ensure_ascii=False)
    assert "CATALOG_STABLE_BODY" not in search_text
    assert "CATALOG_PROJECT_BODY" not in search_text
    assert "CATALOG_TASK_BODY" not in search_text
    assert "Durable user background fact." not in search_text
    distinct = memory.search_memory_references("DISTINCT_FACT", limit=10)["data"]["references"]
    assert {item["description"] for item in distinct} == {
        "DISTINCT_FACT_A_DESC",
        "DISTINCT_FACT_B_DESC",
        "DISTINCT_FACT_C_DESC",
    }
    assert all("content" not in item for item in distinct)
    assert not memory.search_memory_references("GUIDANCE_PREF_ONLY", limit=20)["data"]["references"]
    assert not memory.search_memory_references("GUIDANCE_PROJECT_ONLY", limit=20)["data"]["references"]
    assert not memory.search_memory_references("HIDDEN_TASK_BODY", limit=20)["data"]["references"]

    project_ref = next(item for item in references if item["memory_type"] == "project_summary")
    read = memory.read_memory_reference(project_ref["reference_id"])
    record = read["data"]["reference"]
    assert read["success"] is True
    assert record["reference_id"] == project_ref["reference_id"]
    assert record["memory_type"] == "project_summary" and record["scope"] == "project"
    assert "CATALOG_PROJECT_BODY" in record["content"]
    assert record["source"] == "source:project" and record["updated_at"]

    stable_ref = stable_refs[0]
    stable_record = memory.read_memory_reference(stable_ref["reference_id"])["data"]["reference"]
    stable_body = json.loads(stable_record["content"])
    assert stable_body["content"] == "CATALOG_STABLE_BODY"
    assert stable_body["description"] == "CATALOG_STABLE_DESC"
    assert stable_body["source"] == "user_explicit"
    assert stable_body["source_reference"] == ""
    reloaded = _memory(database_path, "catalog-user", "catalog-project")
    assert any(
        item["description"] == "CATALOG_STABLE_DESC"
        for item in reloaded.get_user_memory()["stable_facts"]
    )

    stable_schema = next(
        item["function"]
        for item in MEMORY_TOOL_SCHEMAS
        if item["function"]["name"] == "remember_stable_fact"
    )
    assert stable_schema["parameters"]["required"] == [
        "content",
        "description",
        "provenance_kind",
    ]

    request = resolve_request_guidance(project_root=root, persistent_memory=memory)
    request_text = _render(list(request.system_messages))
    assert "GUIDANCE_PREF_ONLY" in request_text and "GUIDANCE_PROJECT_ONLY" in request_text


def test_full_record_and_freshness(database_path: Path) -> None:
    memory = _memory(database_path, "body-user", "body-project")
    modified_files = [f"file_{index:02d}_" + chr(65 + index) * 245 for index in range(20)]
    modified_files[-1] = "LAST_CANONICAL_MARKER_" + "Z" * 240
    assert memory.add_task_summary(
        {
            "task_id": "large-history-record",
            "task_type": "build",
            "goal": "large record",
            "result": "completed",
            "summary": "LONG_RECORD_SUMMARY",
            "modified_files": modified_files,
        }
    ).get("success")
    result = memory.search_memory_references("LONG_RECORD_SUMMARY", limit=5)
    reference_id = result["data"]["references"][0]["reference_id"]
    record = memory.read_memory_reference(reference_id)["data"]["reference"]
    assert len(record["content"]) > 4000
    assert "LAST_CANONICAL_MARKER" in record["content"]

    assert memory.add_stable_fact(
        "DELETE_REFERENCE_BODY",
        "Deletable reference",
    ).get("success")
    delete_ref = memory.search_memory_references("DELETE_REFERENCE_BODY", limit=5)["data"]["references"][0]
    before = resolve_memory_reference_guidance(persistent_memory=memory)
    assert before.counts["stable_fact"] == 1
    assert memory.delete_memory_reference(delete_ref["reference_id"]).get("success")
    after = resolve_memory_reference_guidance(persistent_memory=memory)
    assert after.counts["stable_fact"] == 0
    assert memory.read_memory_reference(delete_ref["reference_id"])["success"] is False


def test_scope_isolation(database_path: Path) -> None:
    project_a = _memory(database_path, "scope-user-a", "project-a")
    project_b = _memory(database_path, "scope-user-a", "project-b")
    other_user = _memory(database_path, "scope-user-b", "project-a")
    assert project_a.add_stable_fact("USER_A_SHARED_FACT", "User A shared fact").get("success")
    assert project_a.add_project_summary("/a", "PROJECT_A_SUMMARY", [], "active").get("success")
    assert project_b.add_project_summary("/b", "PROJECT_B_SUMMARY", [], "active").get("success")
    assert other_user.add_stable_fact("USER_B_FACT", "User B fact").get("success")

    a_results = project_a.search_memory_references("SUMMARY FACT", limit=20)["data"]["references"]
    a_project_ref = next(item for item in a_results if item["memory_type"] == "project_summary")
    b_results = project_b.search_memory_references("SUMMARY FACT", limit=20)["data"]["references"]
    other_results = other_user.search_memory_references("SUMMARY FACT", limit=20)["data"]["references"]
    b_text = "\n".join(
        project_b.read_memory_reference(item["reference_id"])["data"]["reference"]["content"]
        for item in b_results
    )
    other_text = "\n".join(
        other_user.read_memory_reference(item["reference_id"])["data"]["reference"]["content"]
        for item in other_results
    )
    assert "USER_A_SHARED_FACT" in b_text and "PROJECT_B_SUMMARY" in b_text
    assert "PROJECT_A_SUMMARY" not in b_text
    assert "USER_A_SHARED_FACT" not in other_text and "USER_B_FACT" in other_text
    assert project_b.read_memory_reference(a_project_ref["reference_id"])["success"] is False
    stable_ref = next(item for item in a_results if item["memory_type"] == "stable_fact")
    assert other_user.read_memory_reference(stable_ref["reference_id"])["success"] is False


def test_agent_react_and_no_hidden_retrieval(manager: WorkspaceManager, root: Path) -> None:
    context = manager.get_context("react-user", "react-project")
    memory = _memory(context.database_path, context.user_id, context.project_id)
    _seed_catalog(memory, prefix="REACT")
    assert memory.add_user_preference("style", "REACT_GUIDANCE_PREF").get("success")
    assert memory.add_project_instruction("REACT_GUIDANCE_PROJECT").get("success")
    modified_files = [f"react_file_{index:02d}_" + ("Q" * 240) for index in range(20)]
    modified_files[-1] = "REACT_LAST_CANONICAL_MARKER_" + ("W" * 230)
    assert memory.add_task_summary({
        "task_id": "react-large-history",
        "task_type": "implementation",
        "goal": "memory reference read",
        "result": "success",
        "summary": "REACT_LONG_RECORD",
        "modified_files": modified_files,
        "hidden_from_memory_prompt": False,
    }).get("success")
    reference_id = memory.search_memory_references("REACT_LONG_RECORD", limit=5)["data"]["references"][0]["reference_id"]

    provider = MockProvider(
        responses=[
            assistant_message("", [_call("search-call", "search_memory_references", {"query": "REACT_LONG_RECORD", "limit": 5})]),
            assistant_message("", [_call("read-call", "read_memory_reference", {"reference_id": reference_id})]),
            assistant_message("react complete"),
        ]
    )
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=4)
    loop.workspace_manager = manager
    _finish_without_persistence(loop)
    assert loop.run("以前做过吗", user_id="react-user", project_id="react-project") == "react complete"
    assert len(provider.calls) == 3
    first = _render(provider.calls[0]["messages"])
    assert "REACT_GUIDANCE_PREF" in first and "REACT_GUIDANCE_PROJECT" in first
    assert "REACT_STABLE_BODY" not in first and "REACT_PROJECT_BODY" not in first and "REACT_TASK_BODY" not in first
    tool_names = {schema["function"]["name"] for schema in provider.calls[0]["tools"]}
    assert {"search_memory_references", "read_memory_reference"}.issubset(tool_names)
    assert "list_memories" not in tool_names
    search_observation = next(message for message in provider.calls[1]["messages"] if message.get("name") == "search_memory_references")
    search_payload = json.loads(search_observation["content"])
    search_data = search_payload.get("data", {})
    search_result = search_payload.get("result", search_data.get("result", search_data))
    assert all("content" not in item for item in search_result["references"])
    read_observation = next(message for message in provider.calls[2]["messages"] if message.get("name") == "read_memory_reference")
    assert "REACT_LONG_RECORD" in read_observation["content"]
    assert "REACT_LAST_CANONICAL_MARKER" in read_observation["content"]
    read_payload = json.loads(read_observation["content"])
    read_data = read_payload.get("data", {})
    read_result = read_payload.get("result", read_data.get("result", read_data))
    assert len(read_result["reference"]["content"]) > 4000
    assert not any(
        message.get("metadata", {}).get("note_type") == "long_term_memory"
        for message in loop.memory.messages
    )

    direct_provider = MockProvider(responses=[assistant_message("no recall needed")])
    with patch("core.loop.WorkspaceManager", return_value=manager):
        direct_loop = AgentLoop(direct_provider, Memory(), max_steps=2)
    direct_loop.workspace_manager = manager
    hidden_calls: list[str] = []
    direct_loop.tools["search_memory_references"] = lambda **_: hidden_calls.append("search") or {"success": True}
    _finish_without_persistence(direct_loop)
    assert direct_loop.run("之前以前做过吗，看看历史任务", user_id="react-user", project_id="react-project") == "no recall needed"
    assert hidden_calls == [] and len(direct_provider.calls) == 1


def test_workspace_switch(manager: WorkspaceManager) -> None:
    old_context = manager.get_context("switch-old-user", "project-a")
    new_context = manager.get_context("switch-new-user", "project-b")
    assert _memory(old_context.database_path, old_context.user_id, old_context.project_id).add_stable_fact(
        "OLD_SCOPE_BODY",
        "Old scope fact",
    ).get("success")
    assert _memory(new_context.database_path, new_context.user_id, new_context.project_id).add_project_summary("/new", "NEW_SCOPE_BODY", [], "active").get("success")

    provider = MockProvider(
        responses=[
            assistant_message("", [_call("switch-call", "switch_workspace", {"user_id": new_context.user_id, "project_id": new_context.project_id})]),
            assistant_message("switched"),
        ]
    )
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=3)
    loop.workspace_manager = manager

    def switch_workspace(user_id: str, project_id: str) -> dict[str, Any]:
        context = manager.get_context(user_id, project_id)
        set_current_workspace(context)
        return {"success": True, "status": "success", "data": {"user_id": user_id, "project_id": project_id, "workspace_id": context.workspace_id}}

    loop.tools["switch_workspace"] = switch_workspace
    _finish_without_persistence(loop)
    assert loop.run("switch", user_id=old_context.user_id, project_id=old_context.project_id) == "switched"
    first = _render(provider.calls[0]["messages"])
    second = _render(provider.calls[1]["messages"])
    assert "<name>stable_fact</name>" in first and "<name>project_summary</name>" not in first
    assert "<name>project_summary</name>" in second and "<name>stable_fact</name>" not in second


def main() -> None:
    original_build_path_context = loop_module.build_path_context
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "HORIZON.md").write_text("ROOT_GUIDANCE", encoding="utf-8")
            database_path = Path(directory) / "horizon.db"
            manager = WorkspaceManager(Path(directory) / "workspaces", database_path=database_path)
            path_context = build_path_context(project_root=root)
            loop_module.build_path_context = lambda *_, **__: path_context
            test_catalog_and_search(database_path, root)
            test_full_record_and_freshness(database_path)
            test_scope_isolation(database_path)
            test_agent_react_and_no_hidden_retrieval(manager, root)
            test_workspace_switch(manager)
    finally:
        loop_module.build_path_context = original_build_path_context
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_memory_reference_on_demand ok")


if __name__ == "__main__":
    main()
