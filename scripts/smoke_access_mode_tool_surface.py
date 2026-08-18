"""Contract smoke for read_only/full_access Agent tool surfaces."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.agent_access_policy as access_policy_module
import tools.registry as registry_module
from core.agent_access_policy import evaluate_agent_tool_access
from core.initial_tool_surface import (
    build_initial_tool_surface,
    initial_agent_turn_tools,
    resolve_permission_tool_schemas,
)
from core.prompt_pack import (
    build_agent_continuation_pack,
    build_initial_agent_turn_pack,
)
from core.tool_execution_authorization import authorize_tool_execution
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


registry_module.get_web_search_provider_status = lambda: SimpleNamespace(
    search_available=True
)


def _schema_names(schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
    return [
        str(schema.get("function", {}).get("name") or "")
        for schema in schemas
        if isinstance(schema, dict)
    ]


def _prompt_text(pack: Any) -> str:
    return "\n".join(str(message.get("content") or "") for message in pack.messages)


def test_initial_surfaces_are_complete_permission_projections() -> None:
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    read_only = build_initial_tool_surface(
        specs,
        schemas,
        access_mode="read_only",
    )
    full_access = build_initial_tool_surface(
        specs,
        schemas,
        access_mode="full_access",
    )
    expected_read = resolve_permission_tool_schemas(
        specs,
        schemas,
        access_mode="read_only",
    )
    expected_full = resolve_permission_tool_schemas(
        specs,
        schemas,
        access_mode="full_access",
    )

    read_names = set(read_only.tool_names)
    full_names = set(full_access.tool_names)
    assert list(read_only.schemas) == list(expected_read)
    assert list(full_access.schemas) == list(expected_full)
    assert _schema_names(initial_agent_turn_tools(read_only)) == list(read_only.tool_names)
    assert _schema_names(initial_agent_turn_tools(full_access)) == list(full_access.tool_names)
    assert {
        "read_file",
        "read_document",
        "web_search",
        "fetch_url",
        "git_status",
        "git_diff",
        "git_log",
    } <= read_names
    assert not {
        "write_file",
        "replace_in_file",
        "sandbox_exec",
        "load_document",
        "load_documents_from_directory",
    } & read_names
    assert {
        "write_file",
        "replace_in_file",
        "sandbox_exec",
        "load_document",
        "load_documents_from_directory",
    } <= full_names
    assert all(specs[name].side_effect is False for name in read_only.tool_names)
    for surface in (read_only, full_access):
        assert surface.source == "registry_availability_permission"


def test_prompt_and_execution_fallback() -> None:
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    read_only = build_initial_tool_surface(specs, schemas, access_mode="read_only")
    full_access = build_initial_tool_surface(specs, schemas, access_mode="full_access")
    state = SimpleNamespace(metadata={}, task_profile=None)

    read_initial = build_initial_agent_turn_pack(
        user_input="inspect",
        tools=initial_agent_turn_tools(read_only),
        access_mode="read_only",
    )
    full_initial = build_initial_agent_turn_pack(
        user_input="implement",
        tools=initial_agent_turn_tools(full_access),
        access_mode="full_access",
    )
    read_continuation = build_agent_continuation_pack(
        user_input="inspect",
        task_state=state,
        tools=list(read_only.schemas),
        memory_messages=[],
        access_mode="read_only",
    )
    full_continuation = build_agent_continuation_pack(
        user_input="implement",
        task_state=state,
        tools=list(full_access.schemas),
        memory_messages=[],
        access_mode="full_access",
    )
    assert "Current access mode is read_only" in _prompt_text(read_initial)
    assert "without performing changes" in _prompt_text(read_continuation)
    assert "Current access mode is full_access" in _prompt_text(full_initial)
    assert "does not bypass Runtime authorization" in _prompt_text(
        full_continuation
    )
    assert read_initial.metadata["access_mode"] == "read_only"
    assert read_continuation.metadata["access_mode"] == "read_only"
    assert full_initial.metadata["access_mode"] == "full_access"
    assert full_continuation.metadata["access_mode"] == "full_access"
    for pack in (read_initial, full_initial, read_continuation, full_continuation):
        text = _prompt_text(pack)
        assert "Current step context" not in text
        assert "current_step" not in text
        assert pack.metadata["tool_surface_mode"] == "registry_availability_permission"

    previous = os.environ.get("AGENT_ACCESS_MODE")
    try:
        os.environ["AGENT_ACCESS_MODE"] = "read_only"
        blocked = authorize_tool_execution(
            task_state=state,
            tool_name="write_file",
        )
        assert blocked.allowed is False
        assert blocked.code == "agent_access_mode_read_only"

        os.environ["AGENT_ACCESS_MODE"] = "full_access"
        full = authorize_tool_execution(
            task_state=state,
            tool_name="write_file",
        )
        assert full.code != "agent_access_mode_read_only"
        assert full.allowed is True
        assert full.reason == "authorized_by_deterministic_execution_safety"
    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous


def test_side_effect_metadata_and_text_rule_isolation() -> None:
    specs = get_unified_tool_specs()
    for name in (
        "read_file",
        "read_document",
        "list_files",
        "get_project_tree",
        "find_files",
        "search_text",
        "web_search",
        "fetch_url",
        "git_status",
        "git_diff",
        "git_log",
        "browser_extract_text",
        "database_select_query",
        "get_context_status",
    ):
        assert specs[name].side_effect is False, name
    for name in (
        "write_file",
        "replace_in_file",
        "sandbox_exec",
        "load_document",
        "load_documents_from_directory",
        "rebuild_chunks_for_document",
        "browser_fill_form",
        "database_execute_write",
        "git_commit",
        "remember_stable_fact",
        "clear_cache",
        "switch_workspace",
    ):
        assert specs[name].side_effect is True, name

    signature = inspect.signature(evaluate_agent_tool_access)
    assert "user_input" not in signature.parameters
    assert "user_goal" not in signature.parameters
    source = inspect.getsource(access_policy_module)
    assert "re.search" not in source
    assert evaluate_agent_tool_access(
        specs["read_file"],
        access_mode="read_only",
    ).allowed
    assert not evaluate_agent_tool_access(
        specs["write_file"],
        access_mode="read_only",
    ).allowed
    assert evaluate_agent_tool_access(
        specs["write_file"],
        access_mode="full_access",
    ).allowed


def main() -> None:
    test_initial_surfaces_are_complete_permission_projections()
    test_prompt_and_execution_fallback()
    test_side_effect_metadata_and_text_rule_isolation()
    print("smoke_access_mode_tool_surface ok")


if __name__ == "__main__":
    main()
