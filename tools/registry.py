"""Central tool registry for the Agent."""

from __future__ import annotations

import json
from typing import Any

from config.settings import settings
from core.mcp_registry import MCPRegistry
from core.tool_risk_registry import (
    BROWSER_READ,
    BROWSER_WRITE,
    DATABASE_READ,
    DATABASE_WRITE,
    EXECUTE,
    GIT_MUTATION,
    GIT_READ,
    NETWORK_READ,
    READ,
    STATE_MUTATION,
    WRITE,
    all_tool_risk_metadata,
    get_tool_risk_metadata,
    mcp_permission_level_to_risk_metadata,
)
from core.tool_spec import ToolKind, ToolPathPolicy, ToolProvider, ToolRisk, ToolSpec
from core.tool_provider_routing import (
    ToolVisibilityMode,
    build_provider_capability_map,
    decide_tool_visibility,
    infer_tool_capability,
    schema_name,
)
from core.status_tool_policy import is_status_query_tool
from core.web_search_provider import get_web_search_provider_status
from tools.browser_tools import BROWSER_TOOL_SCHEMAS, BROWSER_TOOLS
from tools.cache_tools import CACHE_TOOL_SCHEMAS, CACHE_TOOLS
from tools.chunk_tools import CHUNK_TOOL_SCHEMAS, CHUNK_TOOLS
from tools.context_tools import CONTEXT_TOOL_SCHEMAS, CONTEXT_TOOLS
from tools.document_tools import DOCUMENT_TOOL_SCHEMAS, DOCUMENT_TOOLS
from tools.file_tools import FILE_TOOL_SCHEMAS, FILE_TOOLS
from tools.git_tools import GIT_TOOL_SCHEMAS, GIT_TOOLS
from tools.memory_tools import MEMORY_TOOL_SCHEMAS, MEMORY_TOOLS
from tools.project_tools import PROJECT_TOOL_SCHEMAS, PROJECT_TOOLS
from tools.rag_tools import RAG_TOOL_SCHEMAS, RAG_TOOLS
from tools.shell_tools import SHELL_TOOL_SCHEMAS, SHELL_TOOLS
from tools.usage_tools import USAGE_TOOL_SCHEMAS, USAGE_TOOLS
from tools.vector_tools import VECTOR_TOOL_SCHEMAS, VECTOR_TOOLS
from tools.web_tools import WEB_TOOL_SCHEMAS, WEB_TOOLS
from tools.workspace_tools import WORKSPACE_TOOL_SCHEMAS, WORKSPACE_TOOLS


ALL_TOOLS = {
    **FILE_TOOLS,
    **CONTEXT_TOOLS,
    **DOCUMENT_TOOLS,
    **CHUNK_TOOLS,
    **PROJECT_TOOLS,
    **SHELL_TOOLS,
    **GIT_TOOLS,
    **WEB_TOOLS,
    **BROWSER_TOOLS,
    **VECTOR_TOOLS,
    **RAG_TOOLS,
    **MEMORY_TOOLS,
    **WORKSPACE_TOOLS,
    **USAGE_TOOLS,
    **CACHE_TOOLS,
}

ALL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    *FILE_TOOL_SCHEMAS,
    *CONTEXT_TOOL_SCHEMAS,
    *DOCUMENT_TOOL_SCHEMAS,
    *CHUNK_TOOL_SCHEMAS,
    *PROJECT_TOOL_SCHEMAS,
    *SHELL_TOOL_SCHEMAS,
    *GIT_TOOL_SCHEMAS,
    *WEB_TOOL_SCHEMAS,
    *BROWSER_TOOL_SCHEMAS,
    *VECTOR_TOOL_SCHEMAS,
    *RAG_TOOL_SCHEMAS,
    *MEMORY_TOOL_SCHEMAS,
    *WORKSPACE_TOOL_SCHEMAS,
    *USAGE_TOOL_SCHEMAS,
    *CACHE_TOOL_SCHEMAS,
]


def _schema_by_name() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for schema in ALL_TOOL_SCHEMAS:
        name = schema_name(schema)
        if name:
            result[name] = schema
    return result


def _local_tool_spec(name: str, schema: dict[str, Any] | None) -> ToolSpec:
    metadata = get_tool_risk_metadata(name)
    if metadata is None:
        return ToolSpec(
            name=name,
            provider=ToolProvider.LOCAL,
            kind=ToolKind.UNKNOWN,
            input_schema=_schema_parameters(schema),
            description=_schema_description(schema),
            canonical_name=name,
        )
    return _tool_spec_from_risk(
        name=name,
        provider=ToolProvider.LOCAL,
        risk_metadata=metadata,
        input_schema=_schema_parameters(schema),
        description=_schema_description(schema),
        aliases=(),
    )


def _mcp_tool_spec(mcp_spec: Any) -> ToolSpec:
    canonical = str(getattr(mcp_spec, "qualified_name", "") or "")
    name = canonical or str(getattr(mcp_spec, "name", "") or "")
    risk_metadata = mcp_permission_level_to_risk_metadata(
        tool_name=str(getattr(mcp_spec, "name", "") or ""),
        canonical_name=canonical or name,
        permission_level=str(getattr(mcp_spec, "permission_level", "") or "read_only"),
        server_name=str(getattr(mcp_spec, "server_name", "") or ""),
    )
    aliases = tuple(
        item
        for item in (str(getattr(mcp_spec, "name", "") or ""), str(mcp_spec.schema_name()))
        if item and item != name
    )
    return _tool_spec_from_risk(
        name=name,
        provider=ToolProvider.MCP,
        risk_metadata=risk_metadata,
        input_schema=getattr(mcp_spec, "input_schema", None) if isinstance(getattr(mcp_spec, "input_schema", None), dict) else None,
        description=str(getattr(mcp_spec, "description", "") or ""),
        aliases=aliases,
    )


def _alias_spec(spec: ToolSpec, alias_name: str) -> ToolSpec:
    return ToolSpec(
        name=alias_name,
        provider=spec.provider,
        kind=spec.kind,
        capabilities=spec.capabilities,
        input_schema=spec.input_schema,
        description=spec.description,
        side_effect=spec.side_effect,
        mutates_files=spec.mutates_files,
        executes_code=spec.executes_code,
        reads_files=spec.reads_files,
        uses_network=spec.uses_network,
        uses_browser=spec.uses_browser,
        uses_database=spec.uses_database,
        requires_path_guard=spec.requires_path_guard,
        path_policy=spec.path_policy,
        risk=spec.risk,
        aliases=spec.aliases,
        canonical_name=spec.canonical_name or spec.name,
    )


def _tool_spec_from_risk(
    *,
    name: str,
    provider: str,
    risk_metadata: Any,
    input_schema: dict[str, Any] | None,
    description: str,
    aliases: tuple[str, ...],
) -> ToolSpec:
    kind = _kind_from_risk(name, risk_metadata)
    path_policy = _path_policy_from_kind(kind, risk_metadata)
    public_side_effect = bool(risk_metadata.side_effect)
    return ToolSpec(
        name=name,
        provider=provider,
        kind=kind,
        capabilities=_capabilities_from_risk(kind, risk_metadata),
        input_schema=input_schema,
        description=description,
        side_effect=public_side_effect,
        mutates_files=kind == ToolKind.FILE_WRITE,
        executes_code=kind == ToolKind.EXECUTION,
        reads_files=kind == ToolKind.FILE_READ,
        uses_network=bool(risk_metadata.external_io or kind in {ToolKind.WEB_READ, ToolKind.BROWSER_READ, ToolKind.BROWSER_WRITE}),
        uses_browser=kind in {ToolKind.BROWSER_READ, ToolKind.BROWSER_WRITE},
        uses_database=kind in {ToolKind.DATABASE_READ, ToolKind.DATABASE_WRITE},
        requires_path_guard=path_policy in {ToolPathPolicy.FILE_READ, ToolPathPolicy.FILE_WRITE, ToolPathPolicy.PROJECT_PATH},
        path_policy=path_policy,
        risk=_risk_from_kind(kind, risk_metadata),
        aliases=aliases,
        canonical_name=str(getattr(risk_metadata, "canonical_name", "") or name),
    )


def _kind_from_risk(name: str, metadata: Any) -> str:
    if is_status_query_tool(name):
        return ToolKind.STATUS
    if name in {"load_document", "load_documents_from_directory", "rebuild_chunks_for_document"}:
        return ToolKind.FILE_READ
    family = str(metadata.family or "")
    operation = str(metadata.operation or "")
    if operation == WRITE and family in {"file", "mcp_filesystem"}:
        return ToolKind.FILE_WRITE
    if operation == READ and family in {"file", "mcp_filesystem"}:
        return ToolKind.FILE_READ
    if operation == EXECUTE:
        return ToolKind.EXECUTION
    if operation == BROWSER_WRITE:
        return ToolKind.BROWSER_WRITE
    if operation == BROWSER_READ:
        return ToolKind.BROWSER_READ
    if operation == NETWORK_READ:
        return ToolKind.WEB_READ
    if operation == DATABASE_READ:
        return ToolKind.DATABASE_READ
    if operation == DATABASE_WRITE:
        return ToolKind.DATABASE_WRITE
    if operation == GIT_MUTATION:
        return ToolKind.GIT_WRITE
    if operation == GIT_READ:
        return ToolKind.GIT_READ
    if family == "rag":
        return ToolKind.RAG
    if family == "memory":
        return ToolKind.MEMORY
    if family == "cache":
        return ToolKind.CACHE
    if family == "context":
        return ToolKind.CONTEXT
    if family in {"project", "document", "vector"}:
        return ToolKind.PROJECT if family == "project" else ToolKind.RAG
    if provider := str(getattr(metadata, "source", "") or ""):
        if provider.startswith("mcp"):
            return ToolKind.MCP
    return ToolKind.UNKNOWN if operation != STATE_MUTATION else ToolKind.CONTEXT


def _path_policy_from_kind(kind: str, metadata: Any) -> str:
    if kind == ToolKind.FILE_READ:
        if str(getattr(metadata, "family", "") or "") == "project":
            return ToolPathPolicy.PROJECT_PATH
        return ToolPathPolicy.FILE_READ
    if kind == ToolKind.FILE_WRITE:
        return ToolPathPolicy.FILE_WRITE
    if kind in {ToolKind.WEB_READ, ToolKind.BROWSER_READ, ToolKind.BROWSER_WRITE}:
        return ToolPathPolicy.URL
    if kind == ToolKind.EXECUTION:
        return ToolPathPolicy.COMMAND
    return ToolPathPolicy.NONE


def _risk_from_kind(kind: str, metadata: Any) -> str:
    if kind == ToolKind.STATUS:
        return ToolRisk.READ_ONLY
    if kind == ToolKind.BROWSER_WRITE:
        return ToolRisk.BROWSER_SIDE_EFFECT
    if bool(getattr(metadata, "side_effect", False)):
        return ToolRisk.INTERNAL_STATE
    if kind == ToolKind.FILE_READ:
        return ToolRisk.READ_ONLY
    if kind == ToolKind.FILE_WRITE:
        return ToolRisk.FILE_WRITE
    if kind == ToolKind.EXECUTION:
        return ToolRisk.SHELL_EXECUTION if str(metadata.family or "") == "shell" else ToolRisk.CODE_EXECUTION
    if kind in {ToolKind.WEB_READ, ToolKind.BROWSER_READ}:
        return ToolRisk.NETWORK
    if kind == ToolKind.DATABASE_READ:
        return ToolRisk.DATABASE_READ
    if kind == ToolKind.DATABASE_WRITE:
        return ToolRisk.DATABASE_WRITE
    if kind == ToolKind.GIT_WRITE:
        return ToolRisk.GIT_MUTATION
    if kind == ToolKind.MCP:
        return ToolRisk.MCP_EXTERNAL
    if kind != ToolKind.UNKNOWN:
        return ToolRisk.READ_ONLY
    return ToolRisk.UNKNOWN


def _capabilities_from_risk(kind: str, metadata: Any) -> tuple[str, ...]:
    values: list[str] = []
    values.extend(str(item) for item in getattr(metadata, "required_capabilities", ()) or () if item)
    values.extend(str(item) for item in getattr(metadata, "boundary_categories", ()) or () if item)
    capability = infer_tool_capability(str(getattr(metadata, "tool_name", "") or ""))
    if capability:
        values.append(capability)
    tool_name = str(getattr(metadata, "tool_name", "") or "")
    if tool_name in {"load_document", "load_documents_from_directory", "rebuild_chunks_for_document"}:
        values = [value for value in values if value != "file_read"]
        values.extend(("document_load", "state_mutation"))
    elif tool_name == "read_document":
        values = [value for value in values if value != "document_read"]
        values.append("file_read")
    elif kind == ToolKind.FILE_READ:
        values.append("file_read")
    if kind == ToolKind.FILE_WRITE:
        values.append("file_write")
    if kind == ToolKind.EXECUTION:
        values.append("execution")
    return tuple(dict.fromkeys(values))


def _schema_parameters(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict) and isinstance(function.get("parameters"), dict):
        return function["parameters"]
    return None


def _schema_description(schema: dict[str, Any] | None) -> str:
    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict):
        return str(function.get("description") or "")
    return ""


def get_local_tool_specs() -> dict[str, ToolSpec]:
    """Return local built-in tool specs keyed by tool name."""

    schemas = _schema_by_name()
    specs = {
        name: _local_tool_spec(name, schemas.get(name))
        for name in ALL_TOOLS
    }
    for name, metadata in all_tool_risk_metadata().items():
        if name in specs or metadata.source not in {"local", "contract"}:
            continue
        specs[name] = _tool_spec_from_risk(
            name=name,
            provider=ToolProvider.LOCAL,
            risk_metadata=metadata,
            input_schema=_schema_parameters(schemas.get(name)),
            description=_schema_description(schemas.get(name)),
            aliases=(),
        )
    return specs


LOCAL_TOOL_SPECS = get_local_tool_specs()


def get_unified_tool_specs(mcp_registry: MCPRegistry | None = None) -> dict[str, ToolSpec]:
    """Return local specs plus optional MCP specs and aliases."""

    specs = dict(LOCAL_TOOL_SPECS)
    if mcp_registry is None:
        return specs
    for qualified_name, mcp_spec in mcp_registry.tools.items():
        if not mcp_spec.enabled:
            continue
        tool_spec = _mcp_tool_spec(mcp_spec)
        specs[qualified_name] = tool_spec
        schema_alias = mcp_spec.schema_name()
        specs[schema_alias] = _alias_spec(tool_spec, schema_alias)
    for alias_name, qualified_name in _get_mcp_aliases(mcp_registry, set(specs)):
        target = specs.get(qualified_name)
        if target is not None:
            specs[alias_name] = _alias_spec(target, alias_name)
    return specs


def get_tool_spec(name: str, mcp_registry: MCPRegistry | None = None) -> ToolSpec | None:
    """Return a ToolSpec by direct, alias, schema, or canonical name."""

    normalized = normalize_tool_name(name)
    specs = get_unified_tool_specs(mcp_registry)
    return specs.get(name) or specs.get(normalized)


def get_tool_kind(name: str, mcp_registry: MCPRegistry | None = None) -> str:
    spec = get_tool_spec(name, mcp_registry)
    return spec.kind if spec else ToolKind.UNKNOWN


def get_tool_capabilities(name: str, mcp_registry: MCPRegistry | None = None) -> tuple[str, ...]:
    spec = get_tool_spec(name, mcp_registry)
    return spec.capabilities if spec else ()


def is_file_read_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.kind == ToolKind.FILE_READ)


def is_file_write_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.kind == ToolKind.FILE_WRITE)


def is_execution_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.kind == ToolKind.EXECUTION)


def is_browser_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.uses_browser)


def is_database_read_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.kind == ToolKind.DATABASE_READ)


def is_side_effect_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.side_effect)


def is_network_tool(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.uses_network)


def requires_path_guard(name: str, mcp_registry: MCPRegistry | None = None) -> bool:
    spec = get_tool_spec(name, mcp_registry)
    return bool(spec and spec.requires_path_guard)


def get_executable_tool_name(name: str, mcp_registry: MCPRegistry | None = None) -> str:
    """Return the callable key used by get_unified_tools for a tool name or alias."""

    raw = str(name or "").strip()
    if not raw:
        return ""
    if raw in ALL_TOOLS:
        return raw
    normalized = normalize_tool_name(raw)
    if normalized in ALL_TOOLS:
        return normalized
    if mcp_registry is None:
        return normalized
    tools = get_unified_tools(mcp_registry)
    for candidate in (raw, normalized):
        if candidate in tools:
            return candidate
    spec = get_tool_spec(raw, mcp_registry)
    canonical = str(getattr(spec, "canonical_name", "") or "")
    for candidate in (canonical, normalize_tool_name(canonical)):
        if candidate in tools:
            return candidate
    return normalized


def normalize_tool_name(name: str) -> str:
    return str(name or "").strip().split(".")[-1]


def get_local_tool_registry() -> dict[str, Any]:
    """Return local built-in tools."""

    return dict(ALL_TOOLS)


def get_local_tool_schemas() -> list[dict[str, Any]]:
    """Return model-visible local schemas after runtime availability checks."""

    search_available = bool(get_web_search_provider_status().search_available)
    return [
        schema
        for schema in ALL_TOOL_SCHEMAS
        if search_available or schema_name(schema) != "web_search"
    ]


def get_unified_tool_schemas(mcp_registry: MCPRegistry | None = None, *, visibility_mode: ToolVisibilityMode = "default") -> list[dict[str, Any]]:
    """Return local tool schemas plus optional namespaced MCP schemas."""

    schemas = get_local_tool_schemas()
    if mcp_registry is None:
        return schemas
    provider_capabilities = _provider_capabilities(mcp_registry)
    for spec in mcp_registry.tools.values():
        if not spec.enabled:
            continue
        tool_schema = spec.to_tool_schema()
        if _mcp_schema_visible(spec.schema_name(), spec.name, provider_capabilities, visibility_mode):
            schemas.append(tool_schema)
    schemas.extend(
        _get_mcp_alias_schemas(
            mcp_registry,
            {schema["function"]["name"] for schema in schemas if "function" in schema},
            provider_capabilities=provider_capabilities,
            visibility_mode=visibility_mode,
        )
    )
    return schemas


def get_unified_tools(mcp_registry: MCPRegistry | None = None) -> dict[str, Any]:
    """Return local tools plus optional MCP tool wrappers."""

    tools = get_local_tool_registry()
    if mcp_registry is None:
        return tools
    for qualified_name, spec in mcp_registry.tools.items():
        if not spec.enabled:
            continue
        if qualified_name not in tools:
            tools[qualified_name] = _make_mcp_wrapper(mcp_registry, qualified_name)
        schema_name = spec.schema_name()
        if schema_name not in tools:
            tools[schema_name] = _make_mcp_wrapper(mcp_registry, schema_name)
    for alias_name, qualified_name in _get_mcp_aliases(mcp_registry, set(tools)):
        if alias_name not in tools:
            tools[alias_name] = _make_mcp_wrapper(mcp_registry, qualified_name)
    return tools


def _get_mcp_alias_schemas(
    mcp_registry: MCPRegistry,
    reserved_names: set[str],
    *,
    provider_capabilities: dict[str, set[str]] | None = None,
    visibility_mode: ToolVisibilityMode = "default",
) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for alias_name, qualified_name in _get_mcp_aliases(mcp_registry, reserved_names):
        spec = mcp_registry.get_tool(qualified_name)
        if not spec or not spec.enabled:
            continue
        if not _mcp_schema_visible(alias_name, spec.name, provider_capabilities or _provider_capabilities(mcp_registry), visibility_mode):
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": alias_name,
                    "description": f"MCP alias for {spec.qualified_name}",
                    "parameters": spec.input_schema or {"type": "object", "properties": {}},
                },
            }
        )
        reserved_names.add(alias_name)
    return schemas


def _get_mcp_aliases(mcp_registry: MCPRegistry, reserved_names: set[str]) -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = []
    for server in mcp_registry.servers.values():
        if not server.enabled:
            continue
        raw_aliases = server.metadata.get("tool_aliases") if isinstance(server.metadata, dict) else None
        if not isinstance(raw_aliases, dict):
            continue
        for alias_name, tool_name in raw_aliases.items():
            alias = str(alias_name).strip()
            target_tool = str(tool_name).strip()
            if not alias or not target_tool or alias in reserved_names:
                continue
            qualified_name = f"mcp.{server.name}.{target_tool}"
            spec = mcp_registry.get_tool(qualified_name)
            if not spec or spec.server_name != server.name or spec.name != target_tool or not spec.enabled:
                continue
            if alias == spec.schema_name():
                continue
            aliases.append((alias, qualified_name))
            reserved_names.add(alias)
    return aliases


def _provider_capabilities(mcp_registry: MCPRegistry) -> dict[str, set[str]]:
    tools: list[tuple[str, str, str | None]] = []
    tools.extend((schema_name(schema), "local", None) for schema in get_local_tool_schemas())
    for spec in mcp_registry.tools.values():
        if not spec.enabled:
            continue
        tools.append((spec.schema_name(), "mcp", infer_tool_capability(spec.name)))
        server = mcp_registry.servers.get(spec.server_name)
        raw_aliases = server.metadata.get("tool_aliases") if server and isinstance(server.metadata, dict) else None
        if isinstance(raw_aliases, dict):
            for alias_name, tool_name in raw_aliases.items():
                if str(tool_name).strip() == spec.name:
                    tools.append((str(alias_name), "mcp", infer_tool_capability(spec.name)))
    return build_provider_capability_map(tools)  # type: ignore[arg-type]


def _mcp_schema_visible(
    tool_name: str,
    canonical_tool_name: str,
    provider_capabilities: dict[str, set[str]],
    visibility_mode: ToolVisibilityMode,
) -> bool:
    decision = decide_tool_visibility(
        tool_name=tool_name,
        provider="mcp",
        provider_capabilities=provider_capabilities,  # type: ignore[arg-type]
        capability=infer_tool_capability(canonical_tool_name),
        mode=visibility_mode,
    )
    return decision.visibility == "default"


def _make_mcp_wrapper(mcp_registry: MCPRegistry, qualified_name: str) -> Any:
    def wrapper(**arguments: Any) -> dict[str, Any]:
        result = mcp_registry.call_tool(qualified_name, arguments)
        payload = result.to_dict()
        if result.success:
            data, metadata = _truncate_mcp_data(result.data, payload)
            return {"success": True, "data": data, "metadata": metadata}
        return {"success": False, "error": result.error, "error_code": result.error_code, "metadata": payload}

    return wrapper


def _truncate_mcp_data(data: Any, metadata: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    limit = max(int(getattr(settings, "mcp_max_result_chars", 12000) or 12000), 100)
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return data, metadata
    updated = dict(metadata)
    updated["truncated"] = True
    updated["original_chars"] = len(text)
    return {"truncated_text": text[:limit], "truncated": True, "original_chars": len(text)}, updated
