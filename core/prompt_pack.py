"""Stage-specific prompt packs for Horizon Runtime LLM calls."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from core.agent_access_policy import get_agent_access_mode
from core.finalization_context_snapshot import FinalizationContextSnapshot
from core.unicode_safety import sanitize_unicode

SINGLE_FILE_READ_FINAL_CONTENT_CHAR_LIMIT = 12000
REMOVED_CONTINUATION_RUNTIME_SECTIONS = (
    "available_tool_names",
    "recent_observations",
    "capability_surface_summary",
    "full_build_step_contract",
    "generic_task_state",
    "tool_schema_scope_memory_note",
)


@dataclass(frozen=True)
class PromptPack:
    stage: str
    pack_name: str
    messages: list[dict[str, Any]]
    prompt_chars: int
    system_chars: int
    user_chars: int
    message_count: int
    tool_schema_chars: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


def build_initial_agent_turn_pack(
    *,
    user_input: str,
    tools: list[dict[str, Any]],
    memory_messages: list[dict[str, Any]] | None = None,
    request_guidance_messages: list[dict[str, Any]] | None = None,
    reference_guidance_messages: list[dict[str, Any]] | None = None,
    runtime_model_identity_note: str = "",
    current_user_included: bool = False,
    agent_turn_context_summary: dict[str, Any] | None = None,
    previous_context: dict[str, Any] | None = None,
    access_mode: str | None = None,
) -> PromptPack:
    """Build the primary tools-enabled agent turn without an intent JSON contract."""

    mode = get_agent_access_mode(access_mode)
    system = (
        "You are Horizon Runtime's agent.\n"
        + _agent_tool_constraints(mode)
        + "\nAnswer directly when no tool is needed. Otherwise use the supplied structured tools as needed. "
        "After tool results, continue from the actual observations."
    )
    context_notes: list[str] = []
    if agent_turn_context_summary:
        context_notes.append(
            "Current structured runtime context:\n"
            + _json(_compact_mapping(agent_turn_context_summary, 700))
        )
    if previous_context:
        context_notes.append(
            "Previous conversation summary:\n" + _json(_compact_mapping(previous_context, 1000))
        )
    messages = build_agent_turn_messages(
        system_instruction=system,
        memory_messages=memory_messages or [],
        request_guidance_messages=request_guidance_messages or [],
        reference_guidance_messages=reference_guidance_messages or [],
        current_user_input=user_input,
        runtime_model_identity_note=runtime_model_identity_note,
        context_notes=context_notes,
        current_user_included=current_user_included,
    )
    return _make_pack(
        "initial_agent_turn",
        "initial_agent_turn_minimal",
        messages,
        tools=tools,
        metadata={
            "tool_names": [_schema_tool_name(tool) for tool in tools],
            "response_format": None,
            "structured_tools": True,
            "access_mode": mode,
            "tool_surface_mode": "registry_availability_permission",
            **dict(agent_turn_context_summary or {}),
        },
    )


def build_tool_call_pack(
    *,
    user_input: str,
    task_state: Any,
    tools: list[dict[str, Any]],
    memory_messages: list[dict[str, Any]],
    request_guidance_messages: list[dict[str, Any]] | None = None,
    reference_guidance_messages: list[dict[str, Any]] | None = None,
    runtime_model_identity_note: str = "",
    current_user_included: bool = False,
    agent_turn_context_summary: dict[str, Any] | None = None,
    access_mode: str | None = None,
) -> PromptPack:
    """Compatibility wrapper for the unified Agent Continuation prompt."""

    return build_agent_continuation_pack(
        user_input=user_input,
        task_state=task_state,
        tools=tools,
        memory_messages=memory_messages,
        request_guidance_messages=request_guidance_messages,
        reference_guidance_messages=reference_guidance_messages,
        runtime_model_identity_note=runtime_model_identity_note,
        current_user_included=current_user_included,
        agent_turn_context_summary=agent_turn_context_summary,
        access_mode=access_mode,
    )


def build_agent_continuation_pack(
    *,
    user_input: str,
    task_state: Any,
    tools: list[dict[str, Any]],
    memory_messages: list[dict[str, Any]],
    request_guidance_messages: list[dict[str, Any]] | None = None,
    reference_guidance_messages: list[dict[str, Any]] | None = None,
    runtime_model_identity_note: str = "",
    current_user_included: bool = False,
    agent_turn_context_summary: dict[str, Any] | None = None,
    access_mode: str | None = None,
) -> PromptPack:
    """Build one continuation turn that may call tools or answer directly."""

    mode = get_agent_access_mode(access_mode)
    tool_names = [_schema_tool_name(tool) for tool in tools]
    system = (
        "You are Horizon Runtime's agent.\n"
        + _agent_tool_constraints(mode)
        + "\nUse the supplied structured tools when needed. "
        "Treat ToolObservations as the actual results of previous tool calls. "
        "Re-evaluate the original user request after each tool-result batch. "
        "Call additional tools when you decide more work is needed. "
        "Return normal user-facing prose when you decide the request is complete or cannot be continued. "
        "Do not claim tool actions that are not supported by actual ToolObservations."
    )
    messages = build_agent_turn_messages(
        system_instruction=system,
        memory_messages=memory_messages,
        request_guidance_messages=request_guidance_messages or [],
        reference_guidance_messages=reference_guidance_messages or [],
        current_user_input=user_input,
        runtime_model_identity_note=runtime_model_identity_note,
        current_user_included=current_user_included,
    )
    return _make_pack(
        "agent_continuation",
        "agent_continuation",
        messages,
        tools=tools,
        metadata={
            "tool_names": tool_names,
            "tool_surface_mode": "registry_availability_permission",
            "tool_surface_reason": "current_registry_and_permission",
            "tool_surface_fallback_reason": "",
            "context_projection_chars": 0,
            "prompt_overhead_chars": len(system),
            "memory_message_count": len(memory_messages),
            "duplicate_runtime_sections_removed": list(
                REMOVED_CONTINUATION_RUNTIME_SECTIONS
            ),
            "build_step_contract_enabled": False,
            "access_mode": mode,
            **dict(agent_turn_context_summary or {}),
        },
    )


def build_agent_turn_messages(
    *,
    system_instruction: str,
    memory_messages: list[dict[str, Any]],
    request_guidance_messages: list[dict[str, Any]] | None = None,
    reference_guidance_messages: list[dict[str, Any]] | None = None,
    current_user_input: str,
    runtime_model_identity_note: str = "",
    context_notes: list[str] | None = None,
    current_user_included: bool = False,
) -> list[dict[str, Any]]:
    """Build one legal Session message sequence for initial and continuing agent turns."""

    messages: list[dict[str, Any]] = [{"role": "system", "content": str(system_instruction or "").strip()}]
    if runtime_model_identity_note:
        messages.append({"role": "system", "content": str(runtime_model_identity_note)})
    messages.extend(
        dict(message)
        for message in request_guidance_messages or []
        if isinstance(message, dict)
    )
    messages.extend(
        dict(message)
        for message in reference_guidance_messages or []
        if isinstance(message, dict)
    )
    for note in context_notes or []:
        text = str(note or "").strip()
        if text:
            messages.append({"role": "system", "content": text})
    messages.extend(dict(message) for message in memory_messages if isinstance(message, dict))
    if not current_user_included:
        messages.append({"role": "user", "content": str(current_user_input or "")})
    return sanitize_unicode(messages)


def extract_structured_runtime_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the JSON runtime context emitted by the shared Agent Turn pack."""

    marker = "Current structured runtime context:\n"
    decoder = json.JSONDecoder()
    for message in messages:
        content = str(message.get("content") or "")
        marker_index = content.find(marker)
        if marker_index < 0:
            continue
        candidate = content[marker_index + len(marker) :].lstrip()
        try:
            value, _ = decoder.raw_decode(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return sanitize_unicode(value)
    return {}


def build_agent_terminal_synthesis_pack(
    snapshot: FinalizationContextSnapshot,
) -> PromptPack:
    """Build the isolated tools-disabled continuation synthesis pack."""

    system = (
        "You are Horizon Runtime's isolated terminal synthesis turn.\n"
        "All tool execution has ended and tools are disabled.\n"
        "Answer only from the supplied current-request finalization context.\n"
        "Do not request, promise, describe, or emit another ToolCall.\n"
        "Do not invent missing results or expose runtime, trace, planner, schema, or internal identities.\n"
        "When evidence is incomplete, state that only the confirmed results can be summarized.\n"
        "Respond in the same language as the current user request unless another language was explicitly requested."
    )
    return _isolated_finalization_pack(
        snapshot,
        stage="agent_continuation",
        pack_name="agent_terminal_synthesis_isolated",
        system=system,
    )


def build_final_responder_pack(
    snapshot: FinalizationContextSnapshot,
) -> PromptPack:
    """Build the isolated tools-disabled terminal responder pack."""

    system = (
        "You are Horizon Runtime's isolated terminal responder.\n"
        "All tool execution has ended and tools are disabled.\n"
        "Final responder response contract:\n"
        "Answer only from the supplied current-request finalization context.\n"
        "Summarize every independent success and every failed or blocked result.\n"
        "A non-zero command exit is a failure, not a success.\n"
        "Do not request, promise, describe, or emit another ToolCall.\n"
        "Do not invent missing results or expose runtime, trace, planner, schema, or internal identities.\n"
        "Respond in the same language as the current user request unless another language was explicitly requested."
    )
    return _isolated_finalization_pack(
        snapshot,
        stage="final_answer",
        pack_name="terminal_responder_isolated",
        system=system,
    )


def build_final_answer_pack(
    *,
    snapshot: FinalizationContextSnapshot | None = None,
    **_: Any,
) -> PromptPack:
    """Compatibility wrapper requiring an explicit isolated snapshot."""

    if snapshot is None:
        raise ValueError("finalization_context_snapshot_required")
    return build_final_responder_pack(snapshot)


def _isolated_finalization_pack(
    snapshot: FinalizationContextSnapshot,
    *,
    stage: str,
    pack_name: str,
    system: str,
) -> PromptPack:
    if not isinstance(snapshot, FinalizationContextSnapshot):
        raise ValueError("finalization_context_snapshot_required")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _json(snapshot.model_context)},
    ]
    return _make_pack(
        stage,
        pack_name,
        messages,
        tools=[],
        metadata={
            "finalization_isolated": True,
            "snapshot_hash": snapshot.snapshot_hash,
            "result_count": snapshot.result_count,
            "coverage_complete": snapshot.coverage_complete,
            "model_context_chars": snapshot.model_context_chars,
            "tools_disabled": True,
            "tool_names": [],
            "memory_message_count": 0,
        },
    )


def build_single_file_read_final_answer_pack(
    *,
    user_input: str,
    task_state: Any,
    read_observation: dict[str, Any],
) -> PromptPack:
    """Build the minimal final-answer prompt for a completed single-file read."""

    observation_payload = single_file_read_observation_payload(read_observation)
    system = (
        "You are the final-answer stage for a completed single-file read task.\n"
        "Tools are disabled.\n"
        "Respond in the same language as the current user request, unless the user explicitly requests a different language.\n"
        "Answer only from the provided read_file observation and the user's request.\n"
        "Do not request, imply, or describe another tool call.\n"
        "Do not mention internal runtime, planner, tool schemas, or trace.\n"
        "If the file content is truncated, summarize visible content and mention the limitation.\n"
        "Be concise and follow the user's requested output format."
    )
    context = {
        "user_request": str(user_input or "").strip(),
        **observation_payload,
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _json(context)},
    ]
    return _make_pack(
        "single_file_read_final_answer",
        "single_file_read_final_answer_fast",
        messages,
        tools=[],
        metadata={
            "file_path": observation_payload.get("file_path", ""),
            "content_chars": observation_payload.get("visible_chars", 0),
            "content_truncated": observation_payload.get("content_truncated", False),
            "read_status": observation_payload.get("read_status", ""),
        },
    )


def build_single_file_read_agent_continuation_pack(
    *,
    user_input: str,
    task_state: Any,
    tools: list[dict[str, Any]],
    memory_messages: list[dict[str, Any]],
    request_guidance_messages: list[dict[str, Any]] | None = None,
    reference_guidance_messages: list[dict[str, Any]] | None = None,
    runtime_model_identity_note: str = "",
    current_user_included: bool = False,
    access_mode: str | None = None,
) -> PromptPack:
    """Build a compact tools-enabled continuation after one successful read_file."""

    pack = build_agent_continuation_pack(
        user_input=user_input,
        task_state=task_state,
        tools=tools,
        memory_messages=memory_messages,
        request_guidance_messages=request_guidance_messages,
        reference_guidance_messages=reference_guidance_messages,
        runtime_model_identity_note=runtime_model_identity_note,
        current_user_included=current_user_included,
        access_mode=access_mode,
    )
    return PromptPack(
        stage="single_file_read_agent_continuation",
        pack_name="single_file_read_agent_continuation_fast",
        messages=pack.messages,
        prompt_chars=pack.prompt_chars,
        message_count=pack.message_count,
        system_chars=pack.system_chars,
        user_chars=pack.user_chars,
        tool_schema_chars=pack.tool_schema_chars,
        metadata={**pack.metadata, "fast_path": True},
    )


def _access_mode_instruction(access_mode: str) -> str:
    if access_mode == "full_access":
        return (
            "Current access mode is full_access. Use the supplied tools to complete work explicitly requested by the user. "
            "Tool availability does not bypass Runtime authorization, path safety, sensitive-file protection, or dangerous-operation boundaries.\n"
        )
    return (
        "Current access mode is read_only. Use only the supplied local-read and network-read tools to inspect and analyze. "
        "Do not claim that files, code, commands, indexes, workspaces, or local state were changed. "
        "When implementation is requested, inspect relevant information and provide concrete findings and a solution without performing changes.\n"
    )


def _agent_tool_constraints(access_mode: str) -> str:
    return (
        _access_mode_instruction(access_mode)
        + "The tools supplied to this turn are real structured tools. Use only them and match their schemas. "
        "Prefer a dedicated tool over sandbox_exec. "
        "Use an explicit absolute path, or a project-relative path only for the opened project; ask when an existing file location is unknown. "
        "The Runtime may choose its default output directory when none is supplied. "
        "Never claim tool success without a ToolObservation, and never simulate ToolCalls with XML, JSON, or prose. "
        "Use side effects only when the request needs them. Runtime enforces authorization and safety. "
        "Reply in the user's language unless requested otherwise."
    )


def single_file_read_observation_payload(read_observation: dict[str, Any]) -> dict[str, Any]:
    """Extract a bounded payload from the current read_file observation."""

    observation = read_observation if isinstance(read_observation, dict) else {}
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    metadata = observation.get("metadata") if isinstance(observation.get("metadata"), dict) else {}
    args = metadata.get("tool_arguments") if isinstance(metadata.get("tool_arguments"), dict) else {}
    path = str(
        observation.get("path")
        or observation.get("output_path")
        or data.get("path")
        or data.get("output_path")
        or args.get("path")
        or ""
    )
    content = _first_text_value(
        observation.get("content"),
        observation.get("text"),
        observation.get("output_text"),
        data.get("content"),
        data.get("text"),
        data.get("stdout"),
    )
    original_chars = len(content)
    was_truncated = _truthy(observation.get("truncated")) or _truthy(data.get("truncated"))
    bounded_content = content
    if len(bounded_content) > SINGLE_FILE_READ_FINAL_CONTENT_CHAR_LIMIT:
        bounded_content = bounded_content[:SINGLE_FILE_READ_FINAL_CONTENT_CHAR_LIMIT]
        was_truncated = True
    visible_chars = len(bounded_content)
    return {
        "file_path": path,
        "file_content": bounded_content,
        "content_truncated": bool(was_truncated),
        "visible_chars": visible_chars,
        "original_chars": int(_int_value(observation.get("original_chars"), data.get("original_chars")) or original_chars),
        "read_status": str(observation.get("status") or data.get("status") or ""),
        "read_success": observation.get("success") is True,
    }


def build_pack_from_messages(
    *,
    stage: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    **kwargs: Any,
) -> PromptPack:
    if stage == "tool_call":
        return build_tool_call_pack(
            user_input=str(kwargs.get("user_input") or _extract_user_text(messages)),
            task_state=kwargs.get("task_state"),
            tools=tools,
            memory_messages=messages,
        )
    if stage == "final_answer":
        snapshot = kwargs.get("snapshot")
        if not isinstance(snapshot, FinalizationContextSnapshot):
            raise ValueError("finalization_context_snapshot_required")
        return build_final_responder_pack(snapshot)
    return _make_pack(stage or "unknown", f"{stage or 'unknown'}_minimal", messages, tools=tools)


def terminal_final_answer_action_commitment_reason(answer: str) -> str:
    text = str(answer or "").strip().lower()
    if not text:
        return ""
    blocked_phrases = (
        "让我尝试",
        "继续获取",
        "继续读取",
        "我将读取",
        "我会再读取",
        "需要再调用",
        "再调用工具",
        "获取完整内容",
        "fetch more",
        "try to get",
        "continue reading",
        "read the full",
        "get the full",
    )
    compact = " ".join(text.replace("_", " ").split())
    for phrase in blocked_phrases:
        if phrase in compact:
            return "future_tool_action_commitment"
    return ""


def _make_pack(
    stage: str,
    pack_name: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> PromptPack:
    safe_messages = sanitize_unicode([dict(message) for message in messages])
    system_chars = sum(len(str(message.get("content") or "")) for message in safe_messages if message.get("role") == "system")
    user_chars = sum(len(str(message.get("content") or "")) for message in safe_messages if message.get("role") == "user")
    return PromptPack(
        stage=str(stage or "unknown"),
        pack_name=str(pack_name or ""),
        messages=safe_messages,
        prompt_chars=_messages_chars(safe_messages),
        system_chars=system_chars,
        user_chars=user_chars,
        message_count=len(safe_messages),
        tool_schema_chars=0 if not tools else _json_chars(tools),
        metadata=sanitize_unicode(dict(metadata or {})),
    )


def _extract_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _compact_messages(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    text = _json(messages)
    if len(text) <= limit:
        return sanitize_unicode(messages)
    return [{"role": "user", "content": text[:limit]}]


def _compact_mapping(value: dict[str, Any], limit: int) -> dict[str, Any]:
    text = _json(value)
    if len(text) <= limit:
        return sanitize_unicode(dict(value))
    return {"summary": text[:limit], "truncated": True}


def _schema_tool_name(schema: dict[str, Any]) -> str:
    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return ""


def _messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        total += len(content) if isinstance(content, str) else _json_chars(content)
        if message.get("tool_calls"):
            total += _json_chars(message.get("tool_calls"))
    return total


def _json_chars(value: Any) -> int:
    return len(_json(value))


def _first_text_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _int_value(*values: Any) -> int | None:
    for value in values:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _json(value: Any) -> str:
    return json.dumps(sanitize_unicode(value), ensure_ascii=False, separators=(",", ":"), default=str)
