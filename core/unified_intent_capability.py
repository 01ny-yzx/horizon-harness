"""Normalize planner/intent capability metadata for runtime routing.

This module does not call an LLM, choose concrete tools, execute tools, or
decide permissions. It only projects existing structured intent/planner output
into the capability fields used by runtime routing and metrics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from core.unicode_safety import sanitize_unicode


UnifiedIntentCapabilityRoute = Literal["chat_fast_path", "planner_required"]

ALLOWED_CAPABILITIES = {
    "file_read",
    "document_load",
    "file_write",
    "command_exec",
    "network",
    "artifact_output",
    "code_edit",
    "database",
    "browser",
    "mcp",
    "memory",
    "git",
    "validation",
    "multi_step_planning",
}

CAPABILITY_FLAG_FIELDS = {
    "file_read": "requires_file_read",
    "document_load": "requires_document_load",
    "file_write": "requires_file_write",
    "command_exec": "requires_command_exec",
    "network": "requires_network",
    "artifact_output": "requires_artifact_output",
    "code_edit": "requires_code_edit",
    "database": "requires_database",
    "browser": "requires_browser",
    "mcp": "requires_mcp",
    "memory": "requires_memory",
    "git": "requires_git",
    "validation": "requires_validation",
    "multi_step_planning": "requires_multi_step_planning",
}

LEGACY_CAPABILITY_MAP = {
    "file_read": {"file_read"},
    "document_load": {"document_load"},
    "document_search": {"file_read"},
    "rag": {"file_read"},
    "file_write": {"file_write"},
    "coding": {"code_edit"},
    "validation": {"validation", "command_exec"},
    "python_sandbox": {"command_exec"},
    "shell_sandbox": {"command_exec"},
    "git": {"git"},
    "web_search": {"network"},
    "web_fetch": {"network"},
    "browser": {"browser"},
    "browser_extract": {"browser"},
    "browser_links": {"browser"},
    "browser_screenshot": {"browser"},
    "database_read": {"database"},
    "database_schema": {"database"},
    "database_query": {"database"},
    "database_write": {"database"},
    "mcp_tool": {"mcp"},
    "memory_write": {"memory"},
}

TOOL_CAPABILITY_MAP = {
    "read_file": {"file_read"},
    "list_files": {"file_read"},
    "find_files": {"file_read"},
    "search_text": {"file_read"},
    "get_project_tree": {"file_read"},
    "read_document": {"file_read"},
    "load_document": {"document_load"},
    "load_documents_from_directory": {"document_load"},
    "rebuild_chunks_for_document": {"document_load"},
    "write_file": {"file_write"},
    "replace_in_file": {"file_write", "code_edit"},
    "sandbox_exec": {"command_exec"},
    "shell": {"command_exec"},
    "bash": {"command_exec"},
    "command": {"command_exec"},
    "terminal": {"command_exec"},
    "web_search": {"network"},
    "fetch_url": {"network"},
    "browser_extract_text": {"browser"},
    "browser_screenshot": {"browser"},
    "browser_list_links": {"browser"},
    "browser_click_and_extract": {"browser"},
    "rag_query": {"file_read"},
}

CAPABILITY_CANONICAL_TOOL = {
    "document_load": "load_document",
    "file_write": "write_file",
    "artifact_output": "write_file",
    "command_exec": "sandbox_exec",
    "validation": "sandbox_exec",
    "code_edit": "replace_in_file",
}

COMMAND_EXEC_TOOL_ALIASES = {"shell", "bash", "command", "terminal"}
SIDE_EFFECT_CAPABILITIES = {
    "document_load",
    "file_write",
    "artifact_output",
    "command_exec",
    "code_edit",
    "database",
    "browser",
    "git",
    "validation",
}
SIDE_EFFECT_TOOLS = {
    "load_document",
    "load_documents_from_directory",
    "rebuild_chunks_for_document",
    "write_file",
    "replace_in_file",
    "sandbox_exec",
    "git_status",
    "git_diff",
    "git_log",
    "browser_extract_text",
    "browser_screenshot",
    "browser_list_links",
    "browser_click_and_extract",
}


@dataclass(frozen=True)
class UnifiedIntentCapabilityDecision:
    enabled: bool
    route: UnifiedIntentCapabilityRoute
    confidence: float
    reason: str
    planner_would_skip: bool
    required_capabilities: list[str] = field(default_factory=list)
    requires_tools: bool = False
    requires_file_read: bool = False
    requires_document_load: bool = False
    requires_file_write: bool = False
    requires_command_exec: bool = False
    requires_network: bool = False
    requires_artifact_output: bool = False
    requires_code_edit: bool = False
    requires_database: bool = False
    requires_browser: bool = False
    requires_mcp: bool = False
    requires_memory: bool = False
    requires_git: bool = False
    requires_validation: bool = False
    requires_multi_step_planning: bool = False
    runtime_lane_hint: str = ""
    primary_capability: str = ""
    primary_tool: str = ""
    fallback_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


def apply_unified_intent_capability_metadata(task_state: Any) -> UnifiedIntentCapabilityDecision:
    """Normalize capability fields and write them into task_state.metadata."""

    decision = resolve_unified_intent_capability(task_state)
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        return decision
    _write_normalized_primary_tool(metadata, decision.primary_tool)
    metadata.update(
        {
            "pre_router_kind": "unified_intent_capability",
            "pre_router_route": decision.route,
            "pre_router_confidence": decision.confidence,
            "pre_router_reason": decision.reason,
            "planner_would_skip": decision.planner_would_skip,
            "planner_skipped": decision.planner_would_skip,
            "planner_skip_reason": "unified_intent_chat_fast_path"
            if decision.planner_would_skip
            else (decision.fallback_reason or "planner_required"),
            "required_capabilities": list(decision.required_capabilities),
            "requires_tools": decision.requires_tools,
            "requires_file_read": decision.requires_file_read,
            "requires_document_load": decision.requires_document_load,
            "requires_file_write": decision.requires_file_write,
            "requires_command_exec": decision.requires_command_exec,
            "requires_network": decision.requires_network,
            "requires_artifact_output": decision.requires_artifact_output,
            "requires_code_edit": decision.requires_code_edit,
            "requires_database": decision.requires_database,
            "requires_browser": decision.requires_browser,
            "requires_mcp": decision.requires_mcp,
            "requires_memory": decision.requires_memory,
            "requires_git": decision.requires_git,
            "requires_validation": decision.requires_validation,
            "requires_multi_step_planning": decision.requires_multi_step_planning,
            "runtime_lane_hint": decision.runtime_lane_hint,
            "primary_capability": decision.primary_capability,
            "primary_tool": decision.primary_tool,
            "unified_intent_capability": decision.to_dict(),
        }
    )
    if decision.fallback_reason:
        metadata["pre_router_fallback_reason"] = decision.fallback_reason
    _write_capability_tool_plan(metadata, decision)
    if decision.primary_capability == "code_edit" and int(metadata.get("structured_step_count") or 0) <= 1:
        metadata.setdefault("primary_capabilities", ["code_edit"])
        supporting = [str(item) for item in metadata.get("supporting_capabilities") or [] if str(item)]
        for capability in decision.required_capabilities:
            if capability != "code_edit" and capability not in supporting:
                supporting.append(capability)
        metadata["supporting_capabilities"] = supporting
        metadata.setdefault(
            "capability_provenance",
            {"primary": "structured_primary_capability", "supporting": "code_edit_tool_support"},
        )
    _clean_read_only_side_effect_metadata(metadata, decision)
    _sync_read_only_task_profile(task_state, decision)
    return decision


def resolve_unified_intent_capability(task_state: Any) -> UnifiedIntentCapabilityDecision:
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    profile = getattr(task_state, "task_profile", None)
    routing = metadata.get("capability_routing") if isinstance(metadata.get("capability_routing"), dict) else {}
    tool_plan = metadata.get("tool_plan") if isinstance(metadata.get("tool_plan"), dict) else {}
    primary_capability = _normalize(
        tool_plan.get("primary_capability")
        or metadata.get("primary_capability")
    )
    raw_primary_tool = (
        tool_plan.get("primary_tool")
        or metadata.get("primary_tool")
    )
    primary_tool = normalize_primary_tool(primary_capability, str(raw_primary_tool or ""))

    direct_raw = metadata.get("required_capabilities")
    direct_capabilities, invalid = _normalize_direct_capabilities(direct_raw) if direct_raw is not None else ([], [])
    if invalid:
        return _decision(
            route="planner_required",
            confidence=0.0,
            reason="invalid_capability_list",
            planner_would_skip=False,
            required_capabilities=[],
            primary_capability=primary_capability,
            primary_tool=primary_tool,
            fallback_reason="invalid_capability_list",
            metadata={"invalid_capabilities": invalid, "source": "unified_intent"},
        )

    capabilities: set[str] = set(direct_capabilities)
    for source in (
        routing.get("required_capabilities"),
        routing.get("optional_capabilities"),
        tool_plan.get("primary_capability"),
        tool_plan.get("supporting_capabilities"),
        tool_plan.get("fallback_capabilities"),
    ):
        capabilities.update(_map_capabilities(source))
    for source in (
        tool_plan.get("primary_tool"),
        tool_plan.get("tool_priority"),
        tool_plan.get("supporting_tool_priority"),
        tool_plan.get("fallback_tool_priority"),
    ):
        capabilities.update(_map_tools(source))
    capabilities.update(_capabilities_from_profile(profile))

    capabilities = _cleanup_capabilities(
        capabilities,
        primary_capability=primary_capability,
        profile=profile,
        tool_plan=tool_plan,
    )
    primary_capability = primary_capability or _primary_capability_from_capabilities(capabilities)
    primary_tool = normalize_primary_tool(primary_capability, str(raw_primary_tool or primary_tool or ""))
    flags: dict[str, bool] = {}
    for capability, field_name in CAPABILITY_FLAG_FIELDS.items():
        flags[field_name] = flags.get(field_name, False) or capability in capabilities
    tool_required = bool(getattr(profile, "tool_required", False)) if profile is not None else False
    side_effect_required = bool(getattr(profile, "side_effect_required", False)) if profile is not None else False
    if is_read_only_file_task(
        capabilities,
        primary_capability=primary_capability,
        primary_tool=primary_tool,
        tool_plan=tool_plan,
    ):
        tool_required = True
        side_effect_required = False
    requires_tools = bool(capabilities or tool_required)
    lane_hint = _runtime_lane_hint(capabilities)
    if tool_required and not capabilities:
        lane_hint = "plan"
    can_chat = bool(
        not capabilities
        and not tool_required
        and not side_effect_required
        and not _profile_requires_full_agent(profile)
    )
    if can_chat:
        return _decision(
            route="chat_fast_path",
            confidence=float(metadata.get("intent_confidence") or 1.0),
            reason="unified_intent_no_required_capabilities",
            planner_would_skip=True,
            required_capabilities=[],
            runtime_lane_hint="chat",
            primary_capability=primary_capability,
            primary_tool=primary_tool,
            metadata={"source": "unified_intent_capability"},
        )
    return _decision(
        route="planner_required",
        confidence=float(metadata.get("intent_confidence") or 1.0),
        reason="unified_intent_capabilities_required" if capabilities else "unified_intent_requires_planner",
        planner_would_skip=False,
        required_capabilities=sorted(capabilities),
        requires_tools=requires_tools,
        runtime_lane_hint=lane_hint,
        primary_capability=primary_capability,
        primary_tool=primary_tool,
        fallback_reason="" if capabilities else "planner_required",
        metadata={
            "source": "unified_intent_capability",
            "tool_required": tool_required,
            "side_effect_required": side_effect_required,
        },
        **flags,
    )


def canonical_tool_for_capability(capability: str) -> str:
    """Return the default concrete tool for a capability, if one exists."""

    return CAPABILITY_CANONICAL_TOOL.get(_normalize(capability), "")


def normalize_primary_tool(
    primary_capability: str,
    raw_primary_tool: str,
    available_tool_names: set[str] | None = None,
) -> str:
    """Normalize a primary tool name without mixing capability names and tool names."""

    available = {_base_tool_name(item) for item in (available_tool_names or set()) if item}
    raw = _base_tool_name(raw_primary_tool)
    capability = _normalize(primary_capability)
    if raw and raw in available:
        return raw
    if capability == "file_read" and raw == "file_read":
        return "read_file" if not available or "read_file" in available else ""
    if capability == "command_exec" and raw in COMMAND_EXEC_TOOL_ALIASES:
        return "sandbox_exec" if not available or "sandbox_exec" in available else ""
    if raw in ALLOWED_CAPABILITIES:
        mapped = canonical_tool_for_capability(raw)
        if mapped and (not available or mapped in available):
            return mapped
    if raw and raw not in ALLOWED_CAPABILITIES:
        return raw
    mapped = canonical_tool_for_capability(capability)
    if mapped and (not available or mapped in available):
        return mapped
    return ""


def _write_capability_tool_plan(metadata: dict[str, Any], decision: UnifiedIntentCapabilityDecision) -> None:
    if not decision.required_capabilities and not decision.primary_tool:
        return
    plan = metadata.get("tool_plan")
    if not isinstance(plan, dict):
        plan = {}
        metadata["tool_plan"] = plan
    if decision.primary_capability and not plan.get("primary_capability"):
        plan["primary_capability"] = decision.primary_capability
    if decision.primary_tool and (
        not plan.get("primary_tool") or _base_tool_name(plan.get("primary_tool")) in COMMAND_EXEC_TOOL_ALIASES
    ):
        plan["primary_tool"] = decision.primary_tool
    priority = _unique_strings(plan.get("tool_priority"))
    primary_tool = decision.primary_tool
    if primary_tool:
        priority = [item for item in priority if item != primary_tool]
        priority.insert(0, primary_tool)
    canonical = canonical_tool_for_capability(str(plan.get("primary_capability") or decision.primary_capability))
    if canonical and canonical not in priority:
        priority.append(canonical)
    plan["tool_priority"] = priority
    primary_capability = str(plan.get("primary_capability") or decision.primary_capability or "")
    plan["supporting_capabilities"] = _supporting_capabilities_for_plan(
        plan,
        decision,
        primary_capability=primary_capability,
    )
    _sync_command_exec_authorization_capabilities(plan)
    _sync_file_write_authorization_capabilities(plan)
    plan.setdefault("supporting_tool_priority", [item for item in priority if item != plan.get("primary_tool")])
    plan.setdefault("fallback_capabilities", [])
    plan.setdefault("fallback_tool_priority", [])
    plan.setdefault("blocked_capabilities", [])
    plan.setdefault("blocked_tools", [])
    plan.setdefault("fallback_reason", "")
    routing = metadata.get("capability_routing")
    if isinstance(routing, dict):
        routing["required_capabilities"] = list(decision.required_capabilities)
        routing["primary_capability"] = plan.get("primary_capability", "")
        routing["primary_tool"] = plan.get("primary_tool", "")
        routing["tool_plan"] = plan


def _unique_strings(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _sync_command_exec_authorization_capabilities(plan: dict[str, Any]) -> None:
    primary_capability = _normalize(plan.get("primary_capability"))
    primary_tool = _base_tool_name(plan.get("primary_tool"))
    if primary_capability != "command_exec" or primary_tool != "sandbox_exec":
        return
    supporting = _unique_strings(plan.get("supporting_capabilities"))
    if "shell_sandbox" not in supporting:
        supporting.append("shell_sandbox")
    plan["supporting_capabilities"] = supporting


def _sync_file_write_authorization_capabilities(plan: dict[str, Any]) -> None:
    primary_capability = _normalize(plan.get("primary_capability"))
    primary_tool = _base_tool_name(plan.get("primary_tool"))
    if primary_capability != "file_write" or primary_tool != "write_file":
        return
    supporting = _unique_strings(plan.get("supporting_capabilities"))
    if "file_write" not in supporting:
        supporting.append("file_write")
    plan["supporting_capabilities"] = supporting


def _supporting_capabilities_for_plan(
    plan: dict[str, Any],
    decision: UnifiedIntentCapabilityDecision,
    *,
    primary_capability: str,
) -> list[str]:
    supporting = _unique_strings(plan.get("supporting_capabilities"))
    for item in decision.required_capabilities:
        if item == primary_capability:
            continue
        if item not in supporting:
            supporting.append(item)
    return supporting


def _clean_read_only_side_effect_metadata(metadata: dict[str, Any], decision: UnifiedIntentCapabilityDecision) -> None:
    if not _is_pure_file_read_decision(decision):
        return
    metadata["tool_required"] = True
    metadata["side_effect_required"] = False
    metadata["tool_required_effective"] = True
    metadata["side_effect_required_effective"] = False
    routing = metadata.get("capability_routing")
    if isinstance(routing, dict):
        routing["tool_required"] = True
        routing["side_effect_required"] = False
        routing["required_capabilities"] = ["file_read"]
        routing["primary_capability"] = "file_read"
        routing["primary_tool"] = decision.primary_tool
        routing_plan = routing.get("tool_plan")
        if isinstance(routing_plan, dict):
            routing_plan.setdefault("primary_capability", "file_read")
            if decision.primary_tool and not routing_plan.get("primary_tool"):
                routing_plan["primary_tool"] = decision.primary_tool
            routing_plan["side_effect_required"] = False


def _sync_read_only_task_profile(task_state: Any, decision: UnifiedIntentCapabilityDecision) -> None:
    if not _is_pure_file_read_decision(decision):
        return
    profile = getattr(task_state, "task_profile", None)
    if profile is not None:
        task_state.task_profile = replace(
            profile,
            tool_required=True,
            side_effect_required=False,
            execution_mode=str(getattr(profile, "execution_mode", "") or "normal"),
        )


def _is_pure_file_read_decision(decision: UnifiedIntentCapabilityDecision) -> bool:
    capabilities = set(decision.required_capabilities)
    if capabilities != {"file_read"}:
        return False
    return _base_tool_name(decision.primary_tool) in {"", "read_file", "read_document"}


def is_read_only_file_task(
    required_capabilities: Any,
    *,
    primary_capability: str,
    primary_tool: str,
    tool_plan: dict[str, Any] | None,
) -> bool:
    """Return True for a structured file-read task with no write-like plan."""

    capabilities = _map_capabilities(required_capabilities)
    if capabilities != {"file_read"}:
        return False
    if _normalize(primary_capability) not in {"", "file_read"}:
        return False
    if _base_tool_name(primary_tool) not in {"", "read_file", "read_document"}:
        return False
    plan = tool_plan if isinstance(tool_plan, dict) else {}
    plan_capabilities = _map_capabilities(
        _flatten_values(
            plan.get("primary_capability"),
            plan.get("capabilities"),
            plan.get("tool_capabilities"),
            plan.get("supporting_capabilities"),
            plan.get("fallback_capabilities"),
        )
    )
    if plan_capabilities - {"file_read"}:
        return False
    plan_tools = {
        _base_tool_name(value)
        for value in _flatten_values(
            plan.get("primary_tool"),
            plan.get("tool_priority"),
            plan.get("supporting_tool_priority"),
            plan.get("fallback_tool_priority"),
        )
        if _base_tool_name(value)
    }
    return not bool((plan_capabilities & SIDE_EFFECT_CAPABILITIES) or (plan_tools & SIDE_EFFECT_TOOLS))


def _decision(
    *,
    route: UnifiedIntentCapabilityRoute,
    confidence: float,
    reason: str,
    planner_would_skip: bool,
    required_capabilities: list[str],
    requires_tools: bool = False,
    requires_file_read: bool = False,
    requires_document_load: bool = False,
    requires_file_write: bool = False,
    requires_command_exec: bool = False,
    requires_network: bool = False,
    requires_artifact_output: bool = False,
    requires_code_edit: bool = False,
    requires_database: bool = False,
    requires_browser: bool = False,
    requires_mcp: bool = False,
    requires_memory: bool = False,
    requires_git: bool = False,
    requires_validation: bool = False,
    requires_multi_step_planning: bool = False,
    runtime_lane_hint: str = "",
    primary_capability: str = "",
    primary_tool: str = "",
    fallback_reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> UnifiedIntentCapabilityDecision:
    return UnifiedIntentCapabilityDecision(
        enabled=True,
        route=route,
        confidence=confidence,
        reason=reason,
        planner_would_skip=planner_would_skip,
        required_capabilities=list(required_capabilities),
        requires_tools=requires_tools,
        requires_file_read=requires_file_read,
        requires_document_load=requires_document_load,
        requires_file_write=requires_file_write,
        requires_command_exec=requires_command_exec,
        requires_network=requires_network,
        requires_artifact_output=requires_artifact_output,
        requires_code_edit=requires_code_edit,
        requires_database=requires_database,
        requires_browser=requires_browser,
        requires_mcp=requires_mcp,
        requires_memory=requires_memory,
        requires_git=requires_git,
        requires_validation=requires_validation,
        requires_multi_step_planning=requires_multi_step_planning,
        runtime_lane_hint=runtime_lane_hint,
        primary_capability=primary_capability,
        primary_tool=primary_tool,
        fallback_reason=fallback_reason,
        metadata=sanitize_unicode(dict(metadata or {})),
    )


def _normalize_direct_capabilities(value: Any) -> tuple[list[str], list[str]]:
    if value is None or value == "":
        return [], []
    if not isinstance(value, list):
        return [], ["<non_list>"]
    normalized: list[str] = []
    invalid: list[str] = []
    for item in value:
        name = _normalize(item)
        if not name:
            continue
        if name not in ALLOWED_CAPABILITIES:
            invalid.append(name)
        else:
            normalized.append(name)
    return sorted(set(normalized)), sorted(set(invalid))


def _map_capabilities(value: Any) -> set[str]:
    result: set[str] = set()
    for item in _as_list(value):
        name = _normalize(item)
        if name in ALLOWED_CAPABILITIES:
            result.add(name)
        else:
            result.update(LEGACY_CAPABILITY_MAP.get(name, set()))
    return result


def _map_tools(value: Any) -> set[str]:
    result: set[str] = set()
    for item in _as_list(value):
        result.update(TOOL_CAPABILITY_MAP.get(_base_tool_name(item), set()))
    return result


def _capabilities_from_profile(profile: Any) -> set[str]:
    if profile is None:
        return set()
    result: set[str] = set()
    structured_intent_type = _normalize(
        getattr(profile, "structured_intent_type", "")
    )
    if structured_intent_type in {"file_read", "document_read"}:
        result.add("file_read")
    if structured_intent_type == "document_load":
        result.add("document_load")
    if bool(getattr(profile, "needs_document_load", False)):
        result.add("document_load")
    if bool(getattr(profile, "needs_document_retrieval", False)):
        result.add("file_read")
    if bool(getattr(profile, "needs_semantic_retrieval", False)) or bool(getattr(profile, "needs_vector_search", False)):
        result.add("file_read")
    if bool(getattr(profile, "needs_rag", False)):
        result.add("file_read")
    if bool(getattr(profile, "needs_file_output", False)):
        result.update({"artifact_output", "file_write"})
    if bool(getattr(profile, "needs_code_edit", False)):
        result.update({"code_edit", "file_read", "file_write"})
    if bool(getattr(profile, "needs_validation", False)):
        result.update({"validation", "command_exec"})
    if bool(getattr(profile, "needs_git", False)):
        result.add("git")
    if any(
        bool(getattr(profile, name, False))
        for name in ("needs_web", "needs_research", "needs_web_search", "needs_fetch_url")
    ):
        result.add("network")
    if bool(getattr(profile, "needs_browser", False)):
        result.add("browser")
    if bool(getattr(profile, "explicit_tool_intent", False)):
        result.update(_map_tools(getattr(profile, "requested_tool_names", None)))
    return result


def _cleanup_capabilities(
    capabilities: set[str],
    *,
    primary_capability: str,
    profile: Any,
    tool_plan: dict[str, Any],
) -> set[str]:
    result = set(capabilities)
    primary = _normalize(primary_capability)
    profile_needs_code_edit = bool(getattr(profile, "needs_code_edit", False)) if profile is not None else False
    profile_needs_read = bool(
        profile is not None
        and (
            getattr(profile, "needs_document_retrieval", False)
            or getattr(profile, "needs_semantic_retrieval", False)
            or getattr(profile, "needs_vector_search", False)
            or getattr(profile, "needs_rag", False)
        )
    )
    plan_tools = {
        _base_tool_name(item)
        for value in (
            tool_plan.get("primary_tool"),
            tool_plan.get("tool_priority"),
            tool_plan.get("supporting_tool_priority"),
            tool_plan.get("fallback_tool_priority"),
        )
        for item in _as_list(value)
    }
    if primary in {"file_write", "artifact_output"} or result & {"file_write", "artifact_output"}:
        if not profile_needs_code_edit and "replace_in_file" not in plan_tools:
            result.discard("code_edit")
        if not profile_needs_read and not (plan_tools & {"read_file", "read_document", "list_files", "find_files", "search_text", "get_project_tree"}):
            result.discard("file_read")
    if primary in {"command_exec", "validation"} or result & {"command_exec", "validation"}:
        if "mcp" in result and not bool(getattr(profile, "explicit_tool_intent", False) if profile is not None else False):
            result.discard("mcp")
        if not profile_needs_read and not (plan_tools & {"read_file", "read_document", "list_files", "find_files", "search_text", "get_project_tree"}):
            result.discard("file_read")
    return result


def _primary_capability_from_capabilities(capabilities: set[str]) -> str:
    for name in (
        "document_load",
        "file_read",
        "file_write",
        "artifact_output",
        "command_exec",
        "validation",
        "code_edit",
        "network",
        "browser",
        "database",
        "mcp",
        "memory",
        "git",
        "multi_step_planning",
    ):
        if name in capabilities:
            return name
    return ""


def _write_normalized_primary_tool(metadata: dict[str, Any], primary_tool: str) -> None:
    if not primary_tool:
        return
    for key in ("tool_plan",):
        plan = metadata.get(key)
        if isinstance(plan, dict):
            plan["primary_tool"] = primary_tool
    routing = metadata.get("capability_routing")
    if isinstance(routing, dict):
        routing["primary_tool"] = primary_tool
        routing_plan = routing.get("tool_plan")
        if isinstance(routing_plan, dict):
            routing_plan["primary_tool"] = primary_tool


def _profile_requires_full_agent(profile: Any) -> bool:
    if profile is None:
        return False
    return any(
        bool(getattr(profile, name, False))
        for name in (
            "needs_web",
            "needs_research",
            "needs_web_search",
            "needs_fetch_url",
            "needs_browser",
            "needs_rag",
            "needs_document_load",
            "needs_validation",
            "needs_file_output",
            "needs_code_edit",
            "explicit_tool_intent",
        )
    )


def _runtime_lane_hint(capabilities: set[str]) -> str:
    if not capabilities:
        return "chat"
    if capabilities & {"network", "browser"}:
        return "research"
    if capabilities & {"document_load", "file_write", "artifact_output", "code_edit", "command_exec", "validation", "git"}:
        return "build"
    if capabilities & {"file_read", "database", "mcp"}:
        return "explore"
    return "plan"


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if value is None or value == "":
        return []
    return [value]


def _flatten_values(*values: Any) -> list[Any]:
    flattened: list[Any] = []
    for value in values:
        flattened.extend(_as_list(value))
    return flattened


def _base_tool_name(value: Any) -> str:
    return _normalize(str(value or "").rsplit(".", 1)[-1])


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()
