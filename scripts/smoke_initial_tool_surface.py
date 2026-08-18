"""Lightweight checks for the registry-backed initial tool surface."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.initial_tool_surface as surface_module
import tools.registry as registry_module
from core.initial_tool_surface import (
    build_initial_tool_surface,
    initial_agent_turn_tools,
    permission_tool_names,
    permission_tool_schema_chars,
    resolve_permission_tool_schemas,
)
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


registry_module.get_web_search_provider_status = lambda: type(
    "Status", (), {"search_available": True}
)()


def main() -> None:
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    read_only = build_initial_tool_surface(specs, schemas, access_mode="read_only")
    full_access = build_initial_tool_surface(specs, schemas, access_mode="full_access")
    read_permission = resolve_permission_tool_schemas(
        specs,
        schemas,
        access_mode="read_only",
    )
    full_permission = resolve_permission_tool_schemas(
        specs,
        schemas,
        access_mode="full_access",
    )

    assert read_only.tool_names == permission_tool_names(read_permission)
    assert full_access.tool_names == permission_tool_names(full_permission)
    assert full_access.tool_names
    assert "read_document" in read_only.tool_names
    assert "read_document" in full_access.tool_names
    assert "read_document" not in read_only.excluded_tools
    assert "read_document" not in full_access.excluded_tools
    assert "read_document" not in read_only.exclusion_reasons
    assert "read_document" not in full_access.exclusion_reasons
    assert not {"write_file", "replace_in_file", "sandbox_exec"} & set(read_only.tool_names)
    assert {"write_file", "replace_in_file", "sandbox_exec"} <= set(full_access.tool_names)
    assert "web_search" in read_only.tool_names
    assert "fetch_url" in read_only.tool_names
    assert "get_search_status" not in read_only.tool_names
    assert "search_official_docs" not in read_only.tool_names
    assert full_access.schema_chars == len(
        json.dumps(initial_agent_turn_tools(full_access), ensure_ascii=False, separators=(",", ":"), default=str)
    )
    assert permission_tool_schema_chars(full_permission) == full_access.schema_chars
    assert set(full_access.tool_names) <= {schema["function"]["name"] for schema in schemas}
    initial_schemas = {
        schema["function"]["name"]: schema
        for schema in initial_agent_turn_tools(read_only)
        if schema.get("type") == "function"
    }
    assert "read_document" in initial_schemas
    assert initial_schemas["read_document"]["function"]["parameters"]["required"] == ["path"]
    read_document = specs["read_document"]
    assert read_document.provider == "local"
    assert read_document.kind == "file_read"
    assert read_document.risk == "read_only"
    assert read_document.side_effect is False
    assert read_document.reads_files is True
    assert read_document.path_policy == "file_read"
    assert "file_read" in read_document.capabilities
    assert "document_read" not in read_document.capabilities
    assert "document_load" not in read_document.capabilities
    signature = inspect.signature(build_initial_tool_surface)
    assert "user_input" not in signature.parameters
    assert "user_goal" not in signature.parameters
    source = inspect.getsource(surface_module)
    for forbidden in ("re.search", "user_input", "user_goal"):
        assert forbidden not in source
    print("smoke_initial_tool_surface ok")


if __name__ == "__main__":
    main()
