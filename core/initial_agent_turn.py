"""Parsing and structured state projection for the initial agent turn."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from core.build_step_contract import build_step_contract_from_initial_tool_calls
from core.initial_tool_surface import (
    InitialToolSurfaceDecision,
    initial_agent_turn_tools,
)
from core.runtime_metrics import RuntimeMetrics
from core.state import PlanStep, TaskState
from core.task_profile import TaskProfile
from core.tool_boundary import detect_raw_tool_text
from core.tool_call_grants import register_tool_call_grant
from core.tool_schema_scope import schema_tool_name
from core.tool_spec import ToolKind
from core.unicode_safety import sanitize_unicode
from providers.base import call_llm_chat_result, extract_provider_metadata
from providers.openai_compatible import LLMProviderError
from tools.registry import get_executable_tool_name


InitialAgentTurnMode = Literal["direct_answer", "tool_calls", "terminal_failure"]

INITIAL_AGENT_CAPABILITIES = frozenset(
    {
        "file_read", "document_load", "file_write", "command_exec", "code_edit",
        "network", "browser", "database", "mcp", "memory", "git", "validation",
        "artifact_output",
    }
)


@dataclass(frozen=True)
class InitialAgentTurnResult:
    mode: InitialAgentTurnMode
    assistant_message: Any | None = None
    content: str = ""
    tool_calls: tuple[Any, ...] = ()
    surface_tool_names: tuple[str, ...] = ()
    failure_category: str = ""
    failure_reason: str = ""
    error_code: str = ""
    status_code: int | None = None
    retryable: bool = False
    attempt_count: int = 1
    retry_count: int = 0
    repair_reason: str = ""
    user_message: str = ""
    retry_delay_ms: int = 0
    retry_delay_source: str = ""
    retry_wait_applied: bool = False
    retry_skipped_reason: str = ""
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def trace_summary(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                "mode": self.mode,
                "content_chars": len(self.content),
                "tool_call_count": len(self.tool_calls),
                "tool_names": [_tool_call_name(call) for call in self.tool_calls],
                "failure_category": self.failure_category,
                "failure_reason": self.failure_reason,
                "error_code": self.error_code,
                "status_code": self.status_code,
                "retryable": self.retryable,
                "attempt_count": self.attempt_count,
                "retry_count": self.retry_count,
                "repair_reason": self.repair_reason,
                "retry_delay_ms": self.retry_delay_ms,
                "retry_delay_source": self.retry_delay_source,
                "retry_wait_applied": self.retry_wait_applied,
                "retry_skipped_reason": self.retry_skipped_reason,
                "surface_tool_names": list(self.surface_tool_names),
                "provider_metadata": _provider_metadata_summary(self.provider_metadata),
            }
        )


def execute_initial_agent_turn(
    *,
    llm: Any,
    messages: list[dict[str, Any]],
    surface: InitialToolSurfaceDecision,
    options: Any | None = None,
    metrics: RuntimeMetrics | None = None,
    model: str = "",
    attempt_index: int = 1,
) -> InitialAgentTurnResult:
    """Execute one tools-enabled model turn and parse its structured response."""

    if not surface.enabled:
        return _terminal_failure(
            surface, "surface", "initial_tool_surface_unavailable",
            "initial_tool_surface_unavailable", False,
            "当前工具面不可用，任务未执行。",
            attempt_count=0,
        )
    if not provider_supports_tools(llm):
        return _terminal_failure(
            surface, "configuration", "provider_tools_unsupported",
            "provider_tools_unsupported", False,
            "当前模型服务不支持结构化工具调用，无法执行该任务。",
            attempt_count=0,
        )
    tools = initial_agent_turn_tools(surface)
    attempt_handle = (
        metrics.start_llm_attempt(
            messages=messages,
            tools=tools,
            stage="initial_agent_turn",
            model=model,
            options=options,
            attempt_index=attempt_index,
        )
        if metrics is not None
        else None
    )
    try:
        chat_result = call_llm_chat_result(
            llm,
            messages=messages,
            tools=tools,
            options=options,
        )
        assistant_message = chat_result.message
    except LLMProviderError as exc:
        result = _terminal_failure(
            surface, "provider", "provider_exception", str(getattr(exc, "code", "provider_error") or "provider_error"),
            bool(getattr(exc, "retryable", False)), "模型服务调用失败，任务未执行。",
            provider_metadata={
                "response_headers": dict(
                    getattr(exc, "response_headers", {}) or {}
                )
            },
            status_code=getattr(exc, "status_code", None),
        )
        if attempt_handle is not None:
            metrics.finish_llm_attempt(
                attempt_handle,
                provider_success=False,
                runtime_accepted=False,
                error_code=result.error_code,
                status_code=result.status_code,
                retryable=result.retryable,
                provider_metadata={
                    "response_headers": dict(
                        getattr(exc, "response_headers", {}) or {}
                    )
                },
            )
        return result
    except Exception as exc:  # noqa: BLE001 - provider compatibility boundary.
        result = _terminal_failure(
            surface, "provider", "provider_exception", exc.__class__.__name__, False,
            "模型服务调用失败，任务未执行。",
        )
        if attempt_handle is not None:
            metrics.finish_llm_attempt(
                attempt_handle,
                provider_success=False,
                runtime_accepted=False,
                error_code=result.error_code,
            )
        return result
    try:
        result = parse_initial_agent_turn(
            assistant_message,
            surface_tool_names=surface.tool_names,
        )
    except Exception as exc:  # noqa: BLE001 - Runtime must close the provider attempt.
        result = _terminal_failure(
            surface,
            "model_output",
            "initial_agent_turn_parse_exception",
            exc.__class__.__name__,
            True,
            "模型未能返回可执行的结构化结果，任务未执行。",
        )
    if attempt_handle is not None:
        metrics.finish_llm_attempt(
            attempt_handle,
            provider_success=True,
            runtime_accepted=result.mode != "terminal_failure",
            error_code=result.error_code,
            retryable=result.retryable,
            provider_metadata={
                **chat_result.provider_metadata,
                **result.provider_metadata,
            },
            usage=chat_result.usage,
            finish_reason=chat_result.finish_reason,
        )
    return result


def parse_initial_agent_turn(
    assistant_message: Any,
    *,
    surface_tool_names: Sequence[str],
) -> InitialAgentTurnResult:
    """Classify assistant output without interpreting the original user request."""

    surface = tuple(_dedupe(surface_tool_names))
    content = _message_content(assistant_message).strip()
    tool_calls = tuple(_message_tool_calls(assistant_message))
    provider_metadata = extract_provider_metadata(assistant_message)
    if not tool_calls:
        raw_tool_text = detect_raw_tool_text(content, tool_names=surface)
        if raw_tool_text.has_raw_tool_text:
            return _model_output_failure(surface, "raw_tool_markup_without_structured_calls", "raw_tool_markup", provider_metadata)
        if not content:
            return _model_output_failure(surface, "empty_content_without_tool_calls", "empty_initial_agent_turn", provider_metadata)
        return InitialAgentTurnResult(
            mode="direct_answer",
            assistant_message=assistant_message,
            content=content,
            surface_tool_names=surface,
            provider_metadata=provider_metadata,
        )

    parsed_calls: list[tuple[Any, str, dict[str, Any], str]] = []
    for call in tool_calls:
        name = _tool_call_name(call)
        call_id = _tool_call_id(call)
        arguments, error = _tool_call_arguments(call)
        if not name or not call_id or error:
            return _model_output_failure(surface, "invalid_structured_tool_call", error or "missing_tool_call_identity", provider_metadata)
        parsed_calls.append((call, name, arguments, call_id))

    for _, name, _, _ in parsed_calls:
        if name not in surface:
            return _model_output_failure(surface, "tool_not_in_initial_surface", name, provider_metadata)
    return InitialAgentTurnResult(
        mode="tool_calls",
        assistant_message=assistant_message,
        content=content,
        tool_calls=tool_calls,
        surface_tool_names=surface,
        provider_metadata=provider_metadata,
    )


def task_state_from_initial_tool_calls(
    result: InitialAgentTurnResult,
    user_input: str,
    *,
    tool_specs: Mapping[str, Any],
) -> TaskState:
    """Project actual structured calls into one executable TaskState."""

    calls: list[dict[str, Any]] = []
    for call in result.tool_calls:
        name = _tool_call_name(call)
        arguments, _ = _tool_call_arguments(call)
        spec = tool_specs.get(name)
        calls.append(
            {
                "call_id": _tool_call_id(call),
                "tool_name": name,
                "capability": capability_for_tool_spec(spec, tool_name=name),
                "arguments": arguments,
                "side_effect": bool(getattr(spec, "side_effect", False)),
            }
        )
    state = _task_state_from_structured_steps(
        user_input,
        calls,
        source="initial_agent_turn_tool_calls",
        build_contract=build_step_contract_from_initial_tool_calls(calls),
    )
    for index, call in enumerate(calls, start=1):
        name = str(call.get("tool_name") or "")
        spec = tool_specs.get(name)
        register_tool_call_grant(
            state,
            call_id=str(call.get("call_id") or ""),
            provider_call_id=str(call.get("call_id") or ""),
            canonical_name=str(getattr(spec, "canonical_name", "") or name),
            executable_name=get_executable_tool_name(name),
            arguments=dict(call.get("arguments") or {}),
            source="structured",
            step_index=index,
            raw_arguments=json.dumps(call.get("arguments") or {}, ensure_ascii=False, sort_keys=True),
        )
    return state


def task_state_from_initial_direct_answer(user_input: str) -> TaskState:
    profile = _task_profile([], [], side_effect_required=False, source="initial_agent_turn_direct_answer")
    return TaskState.create(
        user_goal=user_input,
        task_type="simple",
        task_profile=profile,
        plan=[PlanStep(index=1, name="initial_agent_turn", instruction="Return the completed user-facing answer.")],
    )


def task_state_from_initial_failure(
    result: InitialAgentTurnResult,
    user_input: str,
) -> TaskState:
    """Create a terminal state without inferring a fallback task contract."""

    profile = _task_profile([], [], side_effect_required=False, source="initial_agent_turn_failure")
    state = TaskState.create(
        user_goal=user_input,
        task_type="simple",
        task_profile=profile,
        plan=[
            PlanStep(
                index=1,
                name="initial_agent_turn_failure",
                instruction="Report the initial runtime failure.",
                status="failed",
                error=result.error_code or result.failure_reason,
            )
        ],
    )
    state.current_phase = "initial_agent_turn_failure"
    state.failed_steps.append("initial_agent_turn_failure")
    state.metadata.update(
        {
            "initial_agent_turn_mode": "terminal_failure",
            "initial_agent_turn_failure_category": result.failure_category,
            "initial_agent_turn_failure_reason": result.failure_reason,
            "initial_agent_turn_error_code": result.error_code,
            "initial_agent_turn_failure_status_code": result.status_code,
            "initial_agent_turn_retryable": result.retryable,
            "initial_agent_turn_attempt_count": result.attempt_count,
            "initial_agent_turn_retry_count": result.retry_count,
            "initial_agent_turn_repair_reason": result.repair_reason,
            "initial_agent_turn_retry_delay_ms": result.retry_delay_ms,
            "initial_agent_turn_retry_delay_source": result.retry_delay_source,
            "initial_agent_turn_retry_wait_applied": result.retry_wait_applied,
            "initial_agent_turn_retry_skipped_reason": result.retry_skipped_reason,
        }
    )
    return state


def capability_for_tool_spec(spec: Any, *, tool_name: str = "") -> str:
    """Map registry metadata to one runtime capability without user-text inference."""

    kind = str(getattr(spec, "kind", "") or "")
    capabilities = tuple(getattr(spec, "capabilities", ()) or ())
    if "document_load" in capabilities:
        return "document_load"
    if kind == ToolKind.STATUS:
        for capability in capabilities:
            normalized = _normalize_capability(str(capability or ""))
            if normalized:
                return normalized
        return "memory"
    if kind == ToolKind.FILE_READ or tool_name in {"list_files", "get_project_tree", "find_files", "search_text"}:
        return "file_read"
    if kind == ToolKind.FILE_WRITE:
        return "code_edit" if tool_name == "replace_in_file" else "file_write"
    if kind == ToolKind.EXECUTION:
        return "command_exec"
    if kind == ToolKind.WEB_READ:
        return "network"
    if kind in {ToolKind.BROWSER_READ, ToolKind.BROWSER_WRITE}:
        return "browser"
    if kind in {ToolKind.DATABASE_READ, ToolKind.DATABASE_WRITE}:
        return "database"
    if kind in {ToolKind.GIT_READ, ToolKind.GIT_WRITE}:
        return "git"
    if kind == ToolKind.MEMORY:
        return "memory"
    if kind == ToolKind.MCP:
        return "mcp"
    for capability in capabilities:
        normalized = _normalize_capability(str(capability or ""))
        if normalized:
            return normalized
    return ""


def provider_supports_tools(llm: Any) -> bool:
    capabilities = getattr(llm, "capabilities", None)
    if capabilities is None:
        config = getattr(llm, "config", None)
        capabilities = getattr(config, "capabilities", None) if config is not None else None
    if capabilities is None:
        return True
    return bool(getattr(capabilities, "supports_tools", True))


def _task_state_from_structured_steps(
    user_input: str,
    steps: list[dict[str, Any]],
    *,
    source: str,
    build_contract: dict[str, Any],
) -> TaskState:
    capabilities = _dedupe(item.get("capability") for item in steps)
    chosen_tools = _dedupe(item.get("tool_name") or item.get("chosen_tool_name") for item in steps)
    primary_capability = capabilities[0] if capabilities else ""
    primary_tool = chosen_tools[0] if chosen_tools else ""
    side_effect_required = any(bool(item.get("side_effect")) for item in steps)
    has_url = any(
        isinstance(item.get("arguments"), dict) and bool(str(item.get("arguments", {}).get("url") or "").strip())
        for item in steps
    )
    profile = _task_profile(
        capabilities,
        chosen_tools,
        side_effect_required=side_effect_required,
        source=source,
        has_url=has_url,
        step_count=len(steps),
    )
    plan_steps = [
        PlanStep(
            index=index,
            name=f"structured_step_{index}",
            instruction=str(item.get("instruction") or f"Execute {item.get('tool_name') or item.get('capability')}"),
        )
        for index, item in enumerate(steps, start=1)
    ] or [PlanStep(index=1, name="initial_agent_turn", instruction="Continue from the structured agent turn.")]
    state = TaskState.create(user_goal=user_input, task_type=profile.task_type, plan=plan_steps, task_profile=profile)
    runtime_lane = _runtime_lane_hint(capabilities, primary_capability, len(steps))
    tool_arguments = dict(steps[0].get("arguments") or {}) if len(steps) == 1 else {
        str(item.get("call_id") or item.get("tool_name") or index): dict(item.get("arguments") or {})
        for index, item in enumerate(steps, start=1)
    }
    supporting = _supporting_capabilities(primary_capability)
    capability_authority = "execution_batch"
    tool_plan = {
        "primary_capability": primary_capability,
        "primary_tool": primary_tool,
        "tool_priority": chosen_tools,
        "supporting_capabilities": supporting,
        "supporting_tool_priority": chosen_tools[1:],
        "fallback_capabilities": [],
        "fallback_tool_priority": [],
        "blocked_capabilities": [],
        "blocked_tools": [],
        "side_effect_required": side_effect_required,
        "source": source,
        "capability_authority": capability_authority,
    }
    state.workflow_kind = "build" if len(steps) > 1 else profile.workflow_kind
    state.metadata.update(
        sanitize_unicode(
            {
                "initial_agent_turn_mode": "tool_calls",
                "capability_authority": capability_authority,
                "normal_completion_owner": "agent_continuation",
                "task_contract_authoritative": False,
                "initial_tool_batch": True,
                "observed_initial_capabilities": capabilities,
                "observed_initial_tool_names": chosen_tools,
                "observed_initial_tool_call_ids": _dedupe(item.get("call_id") for item in steps),
                "required_capabilities": capabilities,
                "primary_capabilities": capabilities if len(steps) > 1 else [primary_capability] if primary_capability else [],
                "supporting_capabilities": supporting,
                "capability_provenance": {
                    "primary": "structured_initial_tool_calls" if len(steps) > 1 else "structured_initial_primary_tool",
                    "supporting": "tool_spec_support",
                },
                "structured_step_count": len(steps),
                "initial_tool_call_count": len(steps),
                "structured_tool_call_count": len(steps),
                "structured_tool_call_ids": _dedupe(item.get("call_id") for item in steps),
                "pending_tool_call_count": len(steps),
                "structured_initial_steps": steps,
                "runtime_lane_hint": runtime_lane,
                "primary_capability": primary_capability,
                "primary_tool": primary_tool,
                "tool_required": bool(capabilities or chosen_tools),
                "side_effect_required": side_effect_required,
                "tool_arguments": tool_arguments,
                "tool_plan": tool_plan,
                "capability_routing": {
                    "required_capabilities": capabilities,
                    "primary_capability": primary_capability,
                    "primary_tool": primary_tool,
                    "tool_plan": tool_plan,
                    "route_source": source,
                    "capability_authority": capability_authority,
                },
            }
        )
    )
    if build_contract:
        state.metadata["build_step_contract"] = build_contract
        state.metadata["build_step_contract_enabled"] = True
        state.metadata["build_step_contract_reason"] = str(build_contract.get("reason") or source)
    return state


def _task_profile(
    capabilities: list[str],
    tools: list[str],
    *,
    side_effect_required: bool,
    source: str,
    has_url: bool = False,
    step_count: int | None = None,
) -> TaskProfile:
    cap_set = set(capabilities)
    task_type = "research" if cap_set & {"network", "browser"} else "coding" if cap_set & {
        "file_write",
        "code_edit",
        "validation",
        "artifact_output",
    } else "simple"
    needs_fetch_url = "fetch_url" in tools
    needs_web_search = "web_search" in tools
    tool_required = bool(capabilities or tools)
    workflow_kind = "build" if int(step_count if step_count is not None else len(tools)) > 1 else "research" if task_type == "research" else "coding" if task_type == "coding" else "simple"
    return TaskProfile(
        task_type=task_type,
        needs_web=bool(cap_set & {"network", "browser"}),
        has_url=bool(has_url),
        has_search_engine_url=False,
        needs_code_edit="code_edit" in cap_set,
        needs_validation="validation" in cap_set,
        needs_git="git" in cap_set,
        user_intent_summary="Structured initial agent turn",
        needs_research=bool(cap_set & {"network", "browser"}),
        needs_web_search=needs_web_search,
        needs_fetch_url=needs_fetch_url,
        needs_browser="browser" in cap_set,
        needs_file_output=bool(cap_set & {"file_write", "artifact_output"}),
        needs_document_load="document_load" in cap_set,
        is_coding_task=task_type == "coding",
        workflow_kind=workflow_kind,
        explicit_tool_intent=bool(tools),
        requested_tool_names=list(tools),
        side_effect_required=side_effect_required,
        tool_required=tool_required,
        execution_mode="normal" if tool_required else "text_only",
        profile_source=source,
        structured_intent_type="tool_call" if tools else "simple_answer",
        structured_task_type=task_type,
        structured_workflow_kind=workflow_kind,
    )


def _supporting_capabilities(primary: str) -> list[str]:
    if primary == "code_edit":
        return ["file_read", "file_write", "command_exec"]
    return []


def _runtime_lane_hint(capabilities: list[str], primary: str, step_count: int) -> str:
    if step_count > 1:
        return "build"
    if primary in {"network", "browser"}:
        return "research"
    if primary == "code_edit":
        return "code_edit"
    if primary == "file_read":
        return "explore"
    if primary == "document_load":
        return "build"
    if primary in {"file_write", "artifact_output", "command_exec", "validation"}:
        return "build"
    return "chat"


def _normalize_capability(value: str) -> str:
    mapping = {
        "web_search": "network",
        "web_fetch": "network",
        "browser_url": "network",
        "browser_status": "browser",
        "shell_sandbox": "command_exec",
        "execution": "command_exec",
        "sandbox": "command_exec",
        "coding": "code_edit",
        "git_read": "git",
        "git_write": "git",
    }
    normalized = str(value or "").strip().lower()
    if normalized in INITIAL_AGENT_CAPABILITIES:
        return normalized
    return mapping.get(normalized, "")


def _message_content(message: Any) -> str:
    value = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    return str(value or "")


def _message_tool_calls(message: Any) -> list[Any]:
    value = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    return list(value or [])


def _tool_call_id(call: Any) -> str:
    value = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
    return str(value or "").strip()


def _tool_call_name(call: Any) -> str:
    function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
    value = function.get("name") if isinstance(function, dict) else getattr(function, "name", "")
    return str(value or "").strip()


def _tool_call_arguments(call: Any) -> tuple[dict[str, Any], str]:
    function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
    raw = function.get("arguments") if isinstance(function, dict) else getattr(function, "arguments", "{}")
    if isinstance(raw, dict):
        return sanitize_unicode(dict(raw)), ""
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}, "tool_call_arguments_invalid_json"
    if not isinstance(value, dict):
        return {}, "tool_call_arguments_not_object"
    return sanitize_unicode(value), ""


def _terminal_failure(
    surface: Sequence[str] | InitialToolSurfaceDecision,
    category: str,
    reason: str,
    error_code: str,
    retryable: bool,
    user_message: str,
    status_code: int | None = None,
    provider_metadata: Mapping[str, Any] | None = None,
    attempt_count: int = 1,
) -> InitialAgentTurnResult:
    names = tuple(getattr(surface, "tool_names", surface) or ())
    return InitialAgentTurnResult(
        mode="terminal_failure",
        surface_tool_names=tuple(names),
        failure_category=category,
        failure_reason=reason,
        error_code=error_code,
        status_code=status_code if isinstance(status_code, int) else None,
        retryable=retryable,
        attempt_count=attempt_count,
        user_message=user_message,
        provider_metadata=dict(provider_metadata or {}),
    )


def _model_output_failure(
    surface: Sequence[str], reason: str, error_code: str, provider_metadata: Mapping[str, Any],
) -> InitialAgentTurnResult:
    return _terminal_failure(
        surface, "model_output", reason, error_code, True,
        "模型未能返回可执行的结构化结果，任务未执行。",
        provider_metadata=provider_metadata,
    )


def _provider_metadata_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        if isinstance(value, str):
            result[str(key)] = value[:500]
        elif isinstance(value, (bool, int, float)) or value is None:
            result[str(key)] = value
    return sanitize_unicode(result)


def _dedupe(values: Sequence[Any] | Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


__all__ = [
    "InitialAgentTurnResult",
    "capability_for_tool_spec",
    "execute_initial_agent_turn",
    "parse_initial_agent_turn",
    "provider_supports_tools",
    "task_state_from_initial_direct_answer",
    "task_state_from_initial_failure",
    "task_state_from_initial_tool_calls",
]
