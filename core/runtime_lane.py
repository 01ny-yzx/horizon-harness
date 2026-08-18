"""Runtime lane classification for one agent task.

The lane is observational metadata only. It describes what the structured
intent says the task wants to do; execution is still controlled by tool plan,
access mode, and safety boundaries elsewhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from core.agent_access_policy import get_agent_access_mode
from core.capability_surface import is_mixed_capability
from core.structured_intent_access import structured_task_profile, structured_tool_plan
from core.unicode_safety import sanitize_unicode


class RuntimeLane(str, Enum):
    CHAT = "chat"
    SINGLE_FILE_READ = "single_file_read"
    FILE_OUTPUT = "file_output"
    COMMAND_EXEC = "command_exec"
    CODE_EDIT = "code_edit"
    PLAN = "plan"
    EXPLORE = "explore"
    BUILD = "build"
    RESEARCH = "research"


@dataclass(frozen=True)
class LaneProfile:
    lane: RuntimeLane
    tool_groups: tuple[str, ...]
    allows_read: bool
    allows_write: bool
    allows_exec: bool
    allows_network: bool
    side_effect_required: bool
    primary_capability: str
    primary_tool: str
    budget_profile: str
    description: str


@dataclass(frozen=True)
class RuntimeLaneDecision:
    lane: RuntimeLane
    reason: str
    tool_groups: tuple[str, ...]
    allows_read: bool
    allows_write: bool
    allows_exec: bool
    allows_network: bool
    access_mode: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["lane"] = self.lane.value
        data["tool_groups"] = list(self.tool_groups)
        return sanitize_unicode(data)


RESEARCH_CAPABILITIES = {
    "web",
    "web_search",
    "research",
    "fetch_url",
    "browser",
    "rag",
}
BUILD_CAPABILITIES = {
    "document_load",
    "coding",
    "file_write",
    "validation",
    "command_exec",
    "python_sandbox",
    "shell_sandbox",
    "command_execution",
    "bash",
}
EXPLORE_CAPABILITIES = {
    "read",
    "file_read",
    "document_retrieval",
    "semantic_retrieval",
    "vector_search",
    "project_inspection",
    "search_text",
}
PLAN_CAPABILITIES = {"plan", "planning", "analysis", "explanation", "text_only", "no_op", "simulate"}
LOCAL_READ_PRIMARY_CAPABILITIES = {
    "read",
    "file_read",
    "document_retrieval",
    "project_inspection",
    "search_text",
}

READ_TOOLS = {
    "list_files",
    "read_file",
    "read_document",
    "get_project_tree",
    "find_files",
    "search_text",
    "list_documents",
    "find_documents",
    "search_document_chunks",
    "get_chunk",
    "list_chunks",
    "semantic_search_chunks",
    "hybrid_search_chunks",
}
EDIT_TOOLS = {"write_file", "replace_in_file"}
EXEC_TOOLS = {"sandbox_exec"}
RESEARCH_TOOLS = {
    "web_search",
    "fetch_url",
    "browser_extract_text",
    "browser_screenshot",
    "browser_list_links",
    "browser_click_and_extract",
    "rag_query",
}
LOCAL_READ_PRIMARY_TOOLS = {
    "list_files",
    "read_file",
    "read_document",
    "get_project_tree",
    "find_files",
    "search_text",
    "list_documents",
    "find_documents",
}
DOCUMENT_LOAD_TOOLS = {
    "load_document",
    "load_documents_from_directory",
}
PLAN_MARKERS = {"plan", "planning", "analysis", "explanation", "simulate", "no_op"}

LANE_PROFILES = {
    RuntimeLane.CHAT: LaneProfile(
        RuntimeLane.CHAT,
        (),
        False,
        False,
        False,
        False,
        False,
        "",
        "",
        "chat_budget",
        "tool-free chat lane",
    ),
    RuntimeLane.SINGLE_FILE_READ: LaneProfile(
        RuntimeLane.SINGLE_FILE_READ,
        ("read",),
        True,
        False,
        False,
        False,
        False,
        "file_read",
        "",
        "single_file_read_budget",
        "single local file read lane",
    ),
    RuntimeLane.FILE_OUTPUT: LaneProfile(
        RuntimeLane.FILE_OUTPUT,
        ("write",),
        False,
        True,
        False,
        False,
        True,
        "file_write",
        "write_file",
        "file_output_budget",
        "file output lane",
    ),
    RuntimeLane.COMMAND_EXEC: LaneProfile(
        RuntimeLane.COMMAND_EXEC,
        ("bash",),
        False,
        False,
        True,
        False,
        True,
        "command_exec",
        "sandbox_exec",
        "command_exec_budget",
        "local command execution lane",
    ),
    RuntimeLane.CODE_EDIT: LaneProfile(
        RuntimeLane.CODE_EDIT,
        ("read", "edit", "bash"),
        True,
        True,
        True,
        False,
        True,
        "code_edit",
        "replace_in_file",
        "code_edit_budget",
        "code edit lane",
    ),
    RuntimeLane.PLAN: LaneProfile(RuntimeLane.PLAN, (), False, False, False, False, False, "", "", "plan_budget", "planning lane"),
    RuntimeLane.EXPLORE: LaneProfile(RuntimeLane.EXPLORE, ("read",), True, False, False, False, False, "", "", "explore_read_budget", "read/explore fallback lane"),
    RuntimeLane.BUILD: LaneProfile(RuntimeLane.BUILD, ("read", "edit", "bash"), True, True, True, False, True, "", "", "build_budget", "complex development fallback lane"),
    RuntimeLane.RESEARCH: LaneProfile(RuntimeLane.RESEARCH, ("web", "rag", "browser"), False, False, False, True, False, "", "", "research_budget", "research fallback lane"),
}


def resolve_runtime_lane(
    task_state: Any,
    tool_plan: dict[str, Any] | None = None,
    access_mode: str | None = None,
) -> RuntimeLaneDecision:
    """Resolve a task lane from existing structured state only."""

    profile = structured_task_profile(task_state)
    plan = dict(tool_plan) if isinstance(tool_plan, dict) else structured_tool_plan(task_state)
    mode = get_agent_access_mode(access_mode)
    signals = _signals(task_state, profile, plan)

    if _mixed_capability_requires_build_fallback(signals):
        return _decision(RuntimeLane.BUILD, "mixed_capability_build_fallback", mode, signals)

    explicit_lane = _resolve_explicit_lane(plan, signals)
    if explicit_lane is not None:
        return _decision(explicit_lane, f"structured_{explicit_lane.value}_profile", mode, signals)
    if _local_file_read_primary_objective(plan, signals):
        metadata = {
            **signals,
            "lane_primary_objective_override": "local_file_read",
            "supporting_research_ignored_for_lane": bool(
                _plan_capabilities(plan) & RESEARCH_CAPABILITIES or _plan_tools(plan) & RESEARCH_TOOLS
            ),
        }
        return _decision(RuntimeLane.EXPLORE, "local_file_read_primary_objective", mode, metadata)
    lane_hint = str(signals.get("runtime_lane_hint") or "")
    if lane_hint == "chat" and not signals.get("required_capabilities"):
        return _decision(RuntimeLane.CHAT, "unified_intent_capability_chat", mode, signals)
    if lane_hint == "research":
        return _decision(RuntimeLane.RESEARCH, "unified_intent_capability_research", mode, signals)
    if lane_hint == "build":
        return _decision(RuntimeLane.BUILD, "unified_intent_capability_build", mode, signals)
    if lane_hint == "explore":
        return _decision(RuntimeLane.EXPLORE, "unified_intent_capability_explore", mode, signals)
    if _has_research_signal(task_state, profile, plan, signals):
        return _decision(RuntimeLane.RESEARCH, "structured_research_signal", mode, signals)
    if _has_build_signal(task_state, profile, plan, signals):
        return _decision(RuntimeLane.BUILD, "structured_build_signal", mode, signals)
    if _has_explore_signal(task_state, profile, plan, signals):
        return _decision(RuntimeLane.EXPLORE, "structured_explore_signal", mode, signals)
    if _has_chat_signal(task_state, profile, plan):
        return _decision(RuntimeLane.CHAT, "structured_chat_signal", mode, signals)
    if _has_plan_signal(task_state, profile, plan, signals):
        return _decision(RuntimeLane.PLAN, "structured_plan_signal", mode, signals)
    return _decision(RuntimeLane.CHAT, "no_structured_tool_or_side_effect_signal", mode, signals)


def _decision(lane: RuntimeLane, reason: str, access_mode: str, signals: dict[str, Any]) -> RuntimeLaneDecision:
    profile = LANE_PROFILES.get(lane)
    groups = profile.tool_groups if profile is not None else _tool_groups(lane)
    primary_capability = str(signals.get("primary_capability") or (profile.primary_capability if profile else "") or "")
    primary_tool = str(signals.get("primary_tool") or (profile.primary_tool if profile else "") or "")
    metadata = {
        **signals,
        "lane_profile": lane.value,
        "lane_reason": reason,
        "allowed_tool_groups": list(groups),
        "budget_profile": profile.budget_profile if profile is not None else "",
        "lane_description": profile.description if profile is not None else "",
        "lane_primary_capability": profile.primary_capability if profile is not None else "",
        "lane_primary_tool": profile.primary_tool if profile is not None else "",
        "primary_capability": primary_capability,
        "primary_tool": primary_tool,
    }
    return RuntimeLaneDecision(
        lane=lane,
        reason=reason,
        tool_groups=groups,
        allows_read=profile.allows_read if profile is not None else "read" in groups,
        allows_write=profile.allows_write if profile is not None else any(group in groups for group in ("write", "edit")),
        allows_exec=profile.allows_exec if profile is not None else "bash" in groups,
        allows_network=profile.allows_network if profile is not None else any(group in groups for group in ("web", "browser")),
        access_mode=access_mode,
        metadata=sanitize_unicode(metadata),
    )


def _tool_groups(lane: RuntimeLane) -> tuple[str, ...]:
    if lane is RuntimeLane.CHAT:
        return ()
    if lane is RuntimeLane.SINGLE_FILE_READ:
        return ("read",)
    if lane is RuntimeLane.FILE_OUTPUT:
        return ("write",)
    if lane is RuntimeLane.COMMAND_EXEC:
        return ("bash",)
    if lane is RuntimeLane.CODE_EDIT:
        return ("read", "edit", "bash")
    if lane is RuntimeLane.PLAN:
        return ()
    if lane is RuntimeLane.EXPLORE:
        return ("read",)
    if lane is RuntimeLane.BUILD:
        return ("read", "edit", "bash")
    if lane is RuntimeLane.RESEARCH:
        return ("web", "rag", "browser")
    return ()


def _signals(task_state: Any, profile: Any, plan: dict[str, Any]) -> dict[str, Any]:
    capabilities = _plan_capabilities(plan)
    tools = _plan_tools(plan)
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    routing = metadata.get("capability_routing") if isinstance(metadata, dict) else {}
    routing = routing if isinstance(routing, dict) else {}
    required_capabilities = metadata.get("required_capabilities") if isinstance(metadata, dict) else []
    return sanitize_unicode(
        {
            "task_type": str(getattr(task_state, "task_type", "") or ""),
            "workflow_kind": str(getattr(task_state, "workflow_kind", "") or ""),
            "profile_task_type": str(getattr(profile, "task_type", "") or ""),
            "profile_workflow_kind": str(getattr(profile, "workflow_kind", "") or ""),
            "structured_task_type": str(getattr(profile, "structured_task_type", "") or ""),
            "structured_intent_type": str(getattr(profile, "structured_intent_type", "") or ""),
            "structured_workflow_kind": str(getattr(profile, "structured_workflow_kind", "") or ""),
            "execution_mode": str(getattr(profile, "execution_mode", "") or ""),
            "side_effect_required": bool(getattr(profile, "side_effect_required", False)),
            "tool_required": bool(getattr(profile, "tool_required", False)),
            "primary_capability": str(plan.get("primary_capability") or routing.get("primary_capability") or ""),
            "primary_tool": str(plan.get("primary_tool") or routing.get("primary_tool") or ""),
            "required_capabilities": _string_list(required_capabilities),
            "effective_required_capabilities": _string_list(
                metadata.get("effective_required_capabilities")
            ) if isinstance(metadata, dict) else [],
            "primary_capabilities": _string_list(metadata.get("primary_capabilities")),
            "supporting_capabilities": _string_list(metadata.get("supporting_capabilities")),
            "structured_step_count": int(metadata.get("structured_step_count") or 0),
            "runtime_lane_hint": str(metadata.get("runtime_lane_hint") or "") if isinstance(metadata, dict) else "",
            "capability_authority": str(
                metadata.get("capability_authority")
                or plan.get("capability_authority")
                or routing.get("capability_authority")
                or ""
            ),
            "initial_tool_batch": bool(metadata.get("initial_tool_batch")),
            "task_contract_authoritative": bool(
                metadata.get("task_contract_authoritative")
            ),
            "plan_capabilities": sorted(capabilities),
            "plan_tools": sorted(tools),
        }
    )


def _has_research_signal(task_state: Any, profile: Any, plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    if _local_file_read_primary_objective(plan, signals) and not _primary_is_research(plan, signals):
        return False
    if profile is not None and any(
        bool(getattr(profile, name, False))
        for name in (
            "needs_research",
            "needs_web_search",
            "needs_fetch_url",
            "needs_browser",
            "needs_rag",
        )
    ):
        return True
    if _primary_is_research(plan, signals):
        return True
    if _primary_is_local_read(plan, signals):
        return False
    if str(getattr(task_state, "task_type", "") or "") == "research":
        return True
    return bool(_plan_capabilities(plan) & RESEARCH_CAPABILITIES or _plan_tools(plan) & RESEARCH_TOOLS)


def _has_build_signal(task_state: Any, profile: Any, plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    if profile is not None and any(
        bool(getattr(profile, name, False))
        for name in (
            "needs_document_load",
            "needs_file_output",
            "needs_code_edit",
            "needs_validation",
        )
    ):
        return True
    return bool(
        _plan_capabilities(plan) & BUILD_CAPABILITIES
        or _plan_tools(plan) & (DOCUMENT_LOAD_TOOLS | EDIT_TOOLS | EXEC_TOOLS)
    )


def _has_explore_signal(task_state: Any, profile: Any, plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    if profile is not None and any(
        bool(getattr(profile, name, False))
        for name in (
            "needs_document_retrieval",
            "needs_semantic_retrieval",
            "needs_vector_search",
        )
    ):
        return True
    capabilities = _plan_capabilities(plan)
    tools = _plan_tools(plan)
    if tools and tools <= READ_TOOLS:
        return True
    if capabilities and capabilities <= EXPLORE_CAPABILITIES:
        return True
    return False


def _has_chat_signal(task_state: Any, profile: Any, plan: dict[str, Any]) -> bool:
    tools = _plan_tools(plan)
    capabilities = _plan_capabilities(plan)
    if tools or capabilities:
        return False
    if profile is None:
        return False

    task_type = str(getattr(task_state, "task_type", "") or "")
    structured_task_type = str(getattr(profile, "structured_task_type", "") or "")
    structured_intent_type = str(getattr(profile, "structured_intent_type", "") or "")

    is_simple_answer = (
        task_type == "simple"
        or structured_task_type in {"simple", "simple_answer"}
        or structured_intent_type in {"simple", "simple_answer"}
    )

    return (
        is_simple_answer
        and not bool(getattr(profile, "tool_required", False))
        and not bool(getattr(profile, "side_effect_required", False))
    )


def _has_plan_signal(task_state: Any, profile: Any, plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    if profile is not None:
        markers = {
            str(getattr(profile, "execution_mode", "") or ""),
            str(getattr(profile, "workflow_kind", "") or ""),
            str(getattr(profile, "structured_task_type", "") or ""),
            str(getattr(profile, "structured_intent_type", "") or ""),
            str(getattr(profile, "structured_workflow_kind", "") or ""),
        }
        if markers & PLAN_MARKERS:
            return True
    if str(getattr(task_state, "workflow_kind", "") or "") in PLAN_MARKERS:
        return True
    capabilities = _plan_capabilities(plan)
    tools = _plan_tools(plan)
    if capabilities and capabilities <= PLAN_CAPABILITIES and not tools:
        return True
    return str(getattr(task_state, "task_type", "") or "") == "coding" and not tools and not capabilities


def _resolve_explicit_lane(plan: dict[str, Any], signals: dict[str, Any]) -> RuntimeLane | None:
    if _mixed_capability_requires_build_fallback(signals):
        return RuntimeLane.BUILD
    if (
        str(signals.get("capability_authority") or "") == "execution_batch"
        or (
            bool(signals.get("initial_tool_batch"))
            and not bool(signals.get("task_contract_authoritative"))
        )
    ):
        return None
    capabilities = set(_string_list(signals.get("required_capabilities"))) | _plan_capabilities(plan)
    tools = _plan_tools(plan)
    primary_capability = _normalize(plan.get("primary_capability") or signals.get("primary_capability"))
    primary_tool = _base_tool_name(plan.get("primary_tool") or signals.get("primary_tool"))

    if not capabilities and not tools and not bool(signals.get("tool_required")) and not bool(signals.get("side_effect_required")):
        return RuntimeLane.CHAT
    if "code_edit" in capabilities or primary_capability == "code_edit" or primary_tool == "replace_in_file":
        return RuntimeLane.CODE_EDIT
    if _is_single_file_read_lane(capabilities, tools, primary_capability, primary_tool, signals):
        return RuntimeLane.SINGLE_FILE_READ
    if _is_file_output_lane(capabilities, tools, primary_capability, primary_tool):
        return RuntimeLane.FILE_OUTPUT
    if _is_command_exec_lane(capabilities, tools, primary_capability, primary_tool):
        return RuntimeLane.COMMAND_EXEC
    return None


def _mixed_capability_requires_build_fallback(signals: dict[str, Any]) -> bool:
    effective_required = _string_list(signals.get("effective_required_capabilities"))
    required = effective_required if effective_required else _string_list(signals.get("required_capabilities"))
    if not is_mixed_capability(required):
        return False
    primary_capability = str(signals.get("primary_capability") or "").strip().lower()
    structured_step_count = int(signals.get("structured_step_count") or 0)
    primary_capabilities = set(_string_list(signals.get("primary_capabilities")))
    if (
        primary_capability == "code_edit"
        and structured_step_count <= 1
        and (not primary_capabilities or primary_capabilities == {"code_edit"})
    ):
        return False
    lane_hint = str(signals.get("runtime_lane_hint") or "").strip().lower()
    if lane_hint == "build":
        return True
    if lane_hint in {"code_edit", "research"}:
        return False
    required_set = set(required)
    if required_set and required_set <= EXPLORE_CAPABILITIES:
        return False
    if "code_edit" in required_set:
        return False
    if required_set & RESEARCH_CAPABILITIES and not (
        required_set & {"file_write", "command_exec", "code_edit", "git", "validation"}
    ):
        return False
    return True


def _is_single_file_read_lane(
    capabilities: set[str],
    tools: set[str],
    primary_capability: str,
    primary_tool: str,
    signals: dict[str, Any],
) -> bool:
    if bool(signals.get("side_effect_required")):
        return False
    if capabilities and capabilities != {"file_read"}:
        return False
    if tools and not tools <= READ_TOOLS:
        return False
    return primary_capability == "file_read" or primary_tool in {"read_file", "read_document"}


def _is_file_output_lane(capabilities: set[str], tools: set[str], primary_capability: str, primary_tool: str) -> bool:
    if capabilities & {"command_exec", "code_edit", "git", "validation"}:
        return False
    if "sandbox_exec" in tools or "replace_in_file" in tools:
        return False
    if primary_capability not in {"file_write", "artifact_output"} and primary_tool != "write_file":
        return False
    return bool(capabilities & {"file_write", "artifact_output"} or primary_tool == "write_file")


def _is_command_exec_lane(capabilities: set[str], tools: set[str], primary_capability: str, primary_tool: str) -> bool:
    if capabilities - {"command_exec", "shell_sandbox"}:
        return False
    if tools - {"sandbox_exec"}:
        return False
    return primary_capability == "command_exec" or primary_tool == "sandbox_exec"


def _plan_capabilities(plan: dict[str, Any]) -> set[str]:
    values = [
        plan.get("primary_capability"),
        *_string_list(plan.get("capabilities")),
        *_string_list(plan.get("tool_capabilities")),
        *_string_list(plan.get("fallback_capabilities")),
        *_string_list(plan.get("supporting_capabilities")),
    ]
    return {_normalize(value) for value in values if _normalize(value)}


def _plan_tools(plan: dict[str, Any]) -> set[str]:
    values = [
        plan.get("primary_tool"),
        *_string_list(plan.get("tool_priority")),
        *_string_list(plan.get("supporting_tool_priority")),
        *_string_list(plan.get("fallback_tool_priority")),
    ]
    return {_base_tool_name(value) for value in values if _base_tool_name(value)}


def _primary_is_local_read(plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    primary_capability = _normalize(plan.get("primary_capability") or signals.get("primary_capability"))
    primary_tool = _base_tool_name(plan.get("primary_tool") or signals.get("primary_tool"))
    return primary_capability in LOCAL_READ_PRIMARY_CAPABILITIES or primary_tool in LOCAL_READ_PRIMARY_TOOLS


def _local_file_read_primary_objective(plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    primary_capability = _normalize(plan.get("primary_capability") or signals.get("primary_capability"))
    primary_tool = _base_tool_name(plan.get("primary_tool") or signals.get("primary_tool"))
    return primary_capability == "file_read" or primary_tool in {"read_file", "read_document"}


def _primary_is_research(plan: dict[str, Any], signals: dict[str, Any]) -> bool:
    primary_capability = _normalize(plan.get("primary_capability") or signals.get("primary_capability"))
    primary_tool = _base_tool_name(plan.get("primary_tool") or signals.get("primary_tool"))
    return primary_capability in RESEARCH_CAPABILITIES or primary_tool in RESEARCH_TOOLS


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _base_tool_name(value: Any) -> str:
    return _normalize(str(value or "").rsplit(".", 1)[-1])


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()
