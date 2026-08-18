"""Request-level context budget and auto-compaction for model calls."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from core.observation_compaction import build_tool_result_card
from core.tool_result_store import ToolResultStore
from core.unicode_safety import sanitize_unicode
from providers.base import LLMCapabilities
from providers.capabilities import resolve_effective_model_limits


@dataclass(frozen=True)
class ContextBudgetConfig:
    enabled: bool = True
    auto_compact: bool = True
    prune_tool_outputs: bool = True
    max_recent_non_system_messages: int = 20
    max_compact_summary_chars: int = 3000
    max_system_note_chars: int = 4000
    max_tool_message_chars: int = 1800


@dataclass(frozen=True)
class ContextBudgetDecision:
    enabled: bool
    action: str
    reason: str
    runtime_lane: str
    original_chars: int
    final_chars: int
    saved_chars: int
    original_message_count: int
    final_message_count: int
    tool_schema_chars: int
    compacted_message_count: int
    pruned_tool_message_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    budget_mode: str = "model_token_budget"
    estimator: str = "conservative_chars_v1"
    model_context_tokens: int | None = None
    model_input_tokens: int | None = None
    reserved_output_tokens: int = 0
    usable_input_tokens: int = 0
    original_estimated_tokens: int = 0
    final_estimated_tokens: int = 0
    saved_estimated_tokens: int = 0
    pressure_detected: bool = False
    protected_recent_tokens: int = 0
    protected_current_task_tokens: int = 0
    raw_model_context_tokens: int = 0
    raw_model_input_tokens: int | None = None
    raw_model_output_tokens: int | None = None
    effective_model_input_tokens: int = 0
    requested_output_tokens: int = 0
    model_limit_correction_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


@dataclass(frozen=True)
class ContextGroup:
    messages: tuple[dict[str, Any], ...]
    kind: str


PRUNED_FAILURE_REASONS = {
    "covered_failed_context_read",
    "covered_failed_observation",
    "duplicate_failed_observation",
    "duplicate_failed_source_observation",
}
DOCUMENT_TOOLS = {"read_document", "load_document", "load_documents_from_directory"}
PATH_KEYS = ("target", "path", "output_path", "url", "command")


def context_budget_config_from_settings(settings: Any) -> ContextBudgetConfig:
    return ContextBudgetConfig(
        enabled=bool(getattr(settings, "context_budget_enabled", True)),
        auto_compact=bool(getattr(settings, "context_budget_auto_compact", True)),
        prune_tool_outputs=bool(getattr(settings, "context_budget_prune_tool_outputs", True)),
    )


def apply_context_budget(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    runtime_lane: str,
    task_state: Any,
    config: ContextBudgetConfig | None = None,
    simple_chat: bool = False,
    model_context_tokens: int | None = None,
    model_input_tokens: int | None = None,
    model_output_tokens: int | None = None,
    requested_output_tokens: int | None = None,
    reserved_output_tokens: int | None = None,
) -> tuple[list[dict[str, Any]], ContextBudgetDecision]:
    """Apply token-aware request compaction while preserving ToolCall groups."""

    cfg = config or ContextBudgetConfig()
    lane = str(runtime_lane or "").strip().lower()
    original_messages = [dict(message) for message in messages]
    tool_chars = _json_chars(tools)
    original_total = _messages_chars(original_messages) + tool_chars
    effective_limits = resolve_effective_model_limits(
        LLMCapabilities(
            max_context_tokens=_positive_int_or_none(model_context_tokens),
            max_input_tokens=_positive_int_or_none(model_input_tokens),
            max_output_tokens=_positive_int_or_none(model_output_tokens),
        ),
        requested_output_tokens if requested_output_tokens is not None else reserved_output_tokens,
    )
    effective_context = effective_limits.raw_context_tokens
    configured_input = effective_limits.raw_input_tokens
    output_reserve = effective_limits.reserved_output_tokens
    usable_tokens = effective_limits.usable_input_tokens
    budget_mode = "model_token_budget"
    original_tokens = estimate_request_tokens(original_messages, tools)
    pressure = original_tokens > usable_tokens

    if not cfg.enabled:
        decision = _decision(
            enabled=False,
            action="keep",
            reason="context_budget_disabled",
            runtime_lane=lane,
            original=original_total,
            final=original_total,
            original_count=len(original_messages),
            final_count=len(original_messages),
            tool_chars=tool_chars,
        )
        return original_messages, _with_token_fields(
            decision,
            budget_mode=budget_mode,
            model_context_tokens=effective_context,
            model_input_tokens=configured_input,
            reserved_output_tokens=output_reserve,
            usable_input_tokens=usable_tokens,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            pressure=False,
            effective_limits=effective_limits,
        )

    if not pressure:
        decision = _decision(
            enabled=True,
            action="keep",
            reason="within_token_budget",
            runtime_lane=lane,
            original=original_total,
            final=original_total,
            original_count=len(original_messages),
            final_count=len(original_messages),
            tool_chars=tool_chars,
            metadata={
                "hard_budget_satisfied": True,
                "externalized_result_count": 0,
                "compacted_tool_call_ids": [],
                "compacted_old_turn_count": 0,
                "content_refs": [],
                **_runtime_state_budget_metadata(task_state),
            },
        )
        return original_messages, _with_token_fields(
            decision,
            budget_mode=budget_mode,
            model_context_tokens=effective_context,
            model_input_tokens=configured_input,
            reserved_output_tokens=output_reserve,
            usable_input_tokens=usable_tokens,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            pressure=False,
            effective_limits=effective_limits,
        )

    if not cfg.auto_compact:
        decision = _decision(
            enabled=True,
            action="keep",
            reason="auto_compact_disabled_over_budget",
            runtime_lane=lane,
            original=original_total,
            final=original_total,
            original_count=len(original_messages),
            final_count=len(original_messages),
            tool_chars=tool_chars,
            metadata={
                "hard_budget_satisfied": False,
                "over_budget_tokens": max(0, original_tokens - usable_tokens),
                **_runtime_state_budget_metadata(task_state),
            },
        )
        return original_messages, _with_token_fields(
            decision,
            budget_mode=budget_mode,
            model_context_tokens=effective_context,
            model_input_tokens=configured_input,
            reserved_output_tokens=output_reserve,
            usable_input_tokens=usable_tokens,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            pressure=True,
            effective_limits=effective_limits,
        )

    kept_messages, compact_meta = _compact_for_token_pressure(
        original_messages,
        tools=tools,
        task_state=task_state,
        lane=lane,
        cfg=cfg,
        usable_tokens=usable_tokens,
    )
    final_total = _messages_chars(kept_messages) + tool_chars
    final_tokens = estimate_request_tokens(kept_messages, tools)
    satisfied = final_tokens <= usable_tokens
    metadata = {
        **compact_meta,
        "simple_chat": simple_chat,
        "hard_budget_satisfied": satisfied,
        "over_budget_tokens": max(0, final_tokens - usable_tokens),
    }
    decision = _decision(
        enabled=True,
        action="compact",
        reason="request_context_compacted" if satisfied else "best_effort_over_context_budget",
        runtime_lane=lane,
        original=original_total,
        final=final_total,
        original_count=len(original_messages),
        final_count=len(kept_messages),
        tool_chars=tool_chars,
        compacted_count=int(compact_meta.get("compacted_message_count", 0)),
        pruned_tool_count=int(compact_meta.get("compacted_tool_result_count", 0)),
        metadata=metadata,
    )
    return kept_messages, _with_token_fields(
        decision,
        budget_mode=budget_mode,
        model_context_tokens=effective_context,
        model_input_tokens=configured_input,
        reserved_output_tokens=output_reserve,
        usable_input_tokens=usable_tokens,
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        pressure=True,
        protected_recent_tokens=int(compact_meta.get("protected_recent_tokens", 0)),
        protected_current_task_tokens=int(compact_meta.get("protected_current_task_tokens", 0)),
        effective_limits=effective_limits,
    )


def estimate_text_tokens(text: str) -> int:
    """Conservatively estimate tokens without a model-specific tokenizer."""

    ascii_chars = sum(1 for char in str(text or "") if ord(char) < 128)
    non_ascii_chars = len(str(text or "")) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4) + non_ascii_chars)


def estimate_request_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    message_tokens = 0
    for message in messages:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)
        message_tokens += estimate_text_tokens(encoded) + 6
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(calls, list):
            message_tokens += 10 * len(calls)
    tool_tokens = estimate_text_tokens(json.dumps(tools or [], ensure_ascii=False, separators=(",", ":"), default=str))
    tool_tokens += 8 * len(tools or [])
    return max(1, math.ceil((message_tokens + tool_tokens) * 1.08))


def _with_token_fields(
    decision: ContextBudgetDecision,
    *,
    budget_mode: str,
    model_context_tokens: int | None,
    model_input_tokens: int | None,
    reserved_output_tokens: int,
    usable_input_tokens: int,
    original_tokens: int,
    final_tokens: int,
    pressure: bool,
    protected_recent_tokens: int = 0,
    protected_current_task_tokens: int = 0,
    effective_limits: Any = None,
) -> ContextBudgetDecision:
    metadata = {
        **dict(decision.metadata or {}),
        "budget_mode": budget_mode,
        "estimator": "conservative_chars_v1",
        "model_context_tokens": model_context_tokens,
        "model_input_tokens": model_input_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "usable_input_tokens": usable_input_tokens,
        "original_estimated_tokens": original_tokens,
        "final_estimated_tokens": final_tokens,
        "saved_estimated_tokens": max(0, original_tokens - final_tokens),
        "pressure_detected": pressure,
        "protected_recent_tokens": protected_recent_tokens,
        "protected_current_task_tokens": protected_current_task_tokens,
        "raw_model_context_tokens": int(getattr(effective_limits, "raw_context_tokens", 0) or 0),
        "raw_model_input_tokens": getattr(effective_limits, "raw_input_tokens", None),
        "raw_model_output_tokens": getattr(effective_limits, "raw_output_tokens", None),
        "effective_model_input_tokens": int(getattr(effective_limits, "effective_input_tokens", 0) or 0),
        "requested_output_tokens": int(getattr(effective_limits, "requested_output_tokens", 0) or 0),
        "model_limit_correction_reason": str(getattr(effective_limits, "correction_reason", "") or ""),
    }
    return replace(
        decision,
        metadata=sanitize_unicode(metadata),
        budget_mode=budget_mode,
        estimator="conservative_chars_v1",
        model_context_tokens=model_context_tokens,
        model_input_tokens=model_input_tokens,
        reserved_output_tokens=reserved_output_tokens,
        usable_input_tokens=usable_input_tokens,
        original_estimated_tokens=original_tokens,
        final_estimated_tokens=final_tokens,
        saved_estimated_tokens=max(0, original_tokens - final_tokens),
        pressure_detected=pressure,
        protected_recent_tokens=protected_recent_tokens,
        protected_current_task_tokens=protected_current_task_tokens,
        raw_model_context_tokens=int(getattr(effective_limits, "raw_context_tokens", 0) or 0),
        raw_model_input_tokens=getattr(effective_limits, "raw_input_tokens", None),
        raw_model_output_tokens=getattr(effective_limits, "raw_output_tokens", None),
        effective_model_input_tokens=int(getattr(effective_limits, "effective_input_tokens", 0) or 0),
        requested_output_tokens=int(getattr(effective_limits, "requested_output_tokens", 0) or 0),
        model_limit_correction_reason=str(getattr(effective_limits, "correction_reason", "") or ""),
    )


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _compact_for_token_pressure(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    task_state: Any,
    lane: str,
    cfg: ContextBudgetConfig,
    usable_tokens: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups = group_protocol_messages([dict(message) for message in messages])
    user_group_indexes = [index for index, group in enumerate(groups) if any(m.get("role") == "user" for m in group.messages)]
    current_start = user_group_indexes[-1] if user_group_indexes else len(groups)
    recent_start = user_group_indexes[-2] if len(user_group_indexes) >= 2 else (user_group_indexes[0] if user_group_indexes else len(groups))
    recent_budget = min(8000, max(2000, int(max(0, usable_tokens) * 0.25)))
    protected_recent_tokens = estimate_request_tokens(_flatten_groups(groups[recent_start:]), []) if recent_start < len(groups) else 0
    protected_current_tokens = estimate_request_tokens(_flatten_groups(groups[current_start:]), []) if current_start < len(groups) else 0
    compacted_ids: list[str] = []
    refs: list[str] = []
    compacted_results = 0

    candidates = [index for index, group in enumerate(groups) if group.kind == "tool_chain" and index < current_start]
    candidates.sort(key=lambda index: (index >= recent_start, -_group_chars(groups[index]), index))
    for index in candidates:
        if estimate_request_tokens(_flatten_groups(groups), tools) <= usable_tokens:
            break
        compacted, ids, found_refs, changed = _compact_tool_chain_group(groups[index], cfg.max_tool_message_chars)
        if changed:
            groups[index] = compacted
            compacted_ids.extend(item for item in ids if item and item not in compacted_ids)
            refs.extend(item for item in found_refs if item and item not in refs)
            compacted_results += len(ids)

    # If recent history itself creates pressure, compact its tool bodies but never remove the protocol group.
    if estimate_request_tokens(_flatten_groups(groups), tools) > usable_tokens:
        for index in range(recent_start, current_start):
            if groups[index].kind != "tool_chain":
                continue
            compacted, ids, found_refs, changed = _compact_tool_chain_group(groups[index], cfg.max_tool_message_chars)
            if changed:
                groups[index] = compacted
                compacted_ids.extend(item for item in ids if item and item not in compacted_ids)
                refs.extend(item for item in found_refs if item and item not in refs)
                compacted_results += len(ids)
            if estimate_request_tokens(_flatten_groups(groups), tools) <= usable_tokens:
                break

    compacted_recent_message_count = 0
    if protected_recent_tokens > recent_budget and recent_start < current_start:
        per_message_chars = max(500, int(recent_budget * 4 / max(1, sum(len(group.messages) for group in groups[recent_start:current_start]))))
        for index in range(recent_start, current_start):
            group = groups[index]
            if group.kind in {"system", "tool_chain"}:
                continue
            compacted_messages: list[dict[str, Any]] = []
            for message in group.messages:
                item = dict(message)
                content = str(item.get("content") or "")
                if len(content) > per_message_chars:
                    item["content"] = _truncate(content, per_message_chars)
                    compacted_recent_message_count += 1
                compacted_messages.append(item)
            groups[index] = ContextGroup(tuple(compacted_messages), group.kind)

    compacted_old_turn_count = 0
    if estimate_request_tokens(_flatten_groups(groups), tools) > usable_tokens and recent_start > 0:
        old_groups = [group for group in groups[:recent_start] if group.kind != "system"]
        system_groups = [group for group in groups[:recent_start] if group.kind == "system"]
        summary_meta: dict[str, Any] = {}
        summary = _build_compact_summary(old_groups, cfg, lane=lane, task_state=task_state, all_groups=groups, summary_metadata=summary_meta)
        summary_group = ContextGroup(({
            "role": "system",
            "content": summary or "Context Compact Summary: older dialogue compacted deterministically.",
            "metadata": {"note_type": "context_compact_summary"},
        },), "system")
        compacted_old_turn_count = sum(1 for group in old_groups if any(m.get("role") == "user" for m in group.messages))
        groups = [*system_groups, summary_group, *groups[recent_start:]]
    else:
        summary_meta = {}

    if estimate_request_tokens(_flatten_groups(groups), tools) > usable_tokens:
        groups = [_clip_system_group(group, min(cfg.max_system_note_chars, 1200)) if group.kind == "system" else group for group in groups]

    final_messages = _flatten_groups(groups)
    return final_messages, {
        "externalized_result_count": len(refs),
        "compacted_tool_call_ids": compacted_ids,
        "compacted_tool_result_count": compacted_results,
        "compacted_old_turn_count": compacted_old_turn_count,
        "compacted_recent_message_count": compacted_recent_message_count,
        "content_refs": refs,
        "compact_summary_reference_count": int(summary_meta.get("reference_count", 0)),
        "history_tool_manifest_ref": str(summary_meta.get("manifest_ref") or ""),
        "history_tool_manifest_sha256": str(summary_meta.get("manifest_sha256") or ""),
        "protected_recent_token_budget": recent_budget,
        "protected_recent_tokens": protected_recent_tokens,
        "protected_current_task_tokens": protected_current_tokens,
        "compacted_message_count": max(0, len(messages) - len(final_messages)) + compacted_results,
        "fallback_reason": "",
    }


def _compact_tool_chain_group(group: ContextGroup, limit: int) -> tuple[ContextGroup, list[str], list[str], bool]:
    messages: list[dict[str, Any]] = []
    call_ids: list[str] = []
    refs: list[str] = []
    changed = False
    for message in group.messages:
        item = dict(message)
        if item.get("role") != "tool":
            messages.append(item)
            continue
        call_id = str(item.get("tool_call_id") or "")
        if call_id:
            call_ids.append(call_id)
        compacted_content, content_refs = _tool_result_card(str(item.get("content") or ""), limit)
        if compacted_content != str(item.get("content") or ""):
            item["content"] = compacted_content
            changed = True
        refs.extend(ref for ref in content_refs if ref not in refs)
        messages.append(item)
    return ContextGroup(tuple(messages), group.kind), call_ids, refs, changed


def _tool_result_card(content: str, limit: int) -> tuple[str, list[str]]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        if len(content) <= limit:
            return content, []
        return json.dumps({"status": "unknown", "compacted": True, "preview": _truncate(content, limit)}, ensure_ascii=False), []
    if not isinstance(payload, dict):
        return content if len(content) <= limit else json.dumps({"compacted": True, "preview": _truncate(content, limit)}, ensure_ascii=False), []
    card = build_tool_result_card(payload, max_preview_chars=limit)
    refs = _collect_content_refs(card)
    return json.dumps(sanitize_unicode(card), ensure_ascii=False, separators=(",", ":")), refs


def _collect_content_refs(value: Any) -> list[str]:
    refs: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if (key == "content_ref" or key.endswith("_ref")) and isinstance(child, str) and child and child not in refs:
                    refs.append(child)
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return refs


def group_protocol_messages(messages: list[dict[str, Any]]) -> list[ContextGroup]:
    groups: list[ContextGroup] = []
    index = 0
    while index < len(messages):
        message = dict(messages[index])
        role = message.get("role")
        tool_calls = message.get("tool_calls") if role == "assistant" else None
        if tool_calls:
            expected = len(tool_calls)
            grouped = [message]
            offset = 1
            while offset <= expected and index + offset < len(messages):
                candidate = dict(messages[index + offset])
                if candidate.get("role") != "tool":
                    break
                grouped.append(candidate)
                offset += 1
            groups.append(ContextGroup(tuple(grouped), "tool_chain"))
            index += len(grouped)
            continue
        if role == "tool":
            groups.append(ContextGroup((message,), "orphan_tool"))
        elif role == "system":
            groups.append(ContextGroup((message,), "system"))
        else:
            groups.append(ContextGroup((message,), str(role or "message")))
        index += 1
    return groups


def _apply_request_level_prune(
    messages: list[dict[str, Any]],
    *,
    runtime_lane: str,
    task_state: Any,
    tools: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prune stale tool outputs before compact decisions without deleting protocol messages."""

    tool_chars = _json_chars(tools or [])
    pre_chars = _messages_chars(messages) + tool_chars
    available_tools = _tool_names_from_schemas(tools or [])
    groups = group_protocol_messages(messages)
    success_targets = _success_targets(groups)
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    result: list[dict[str, Any]] = []
    pruned_count = 0
    unavailable_count = 0
    covered_count = 0
    for message in messages:
        item = dict(message)
        if item.get("role") != "tool":
            result.append(item)
            continue
        payload = _parse_payload(str(item.get("content") or ""))
        tool_name = str(item.get("name") or (payload or {}).get("tool") or "").rsplit(".", 1)[-1]
        if payload is None or _payload_success(payload):
            result.append(item)
            continue
        prune_reason = ""
        if available_tools and tool_name and tool_name not in available_tools:
            prune_reason = "request_level_stale_or_unavailable_failure"
            unavailable_count += 1
        elif _is_pruned_or_covered_failure(payload, tool_name, success_targets):
            prune_reason = "request_level_stale_or_covered_failure"
            covered_count += 1
        elif _explore_read_file_primary(task_state, metadata) and tool_name in DOCUMENT_TOOLS:
            prune_reason = "request_level_stale_or_unavailable_failure"
            unavailable_count += 1
        if prune_reason:
            item["content"] = _placeholder_tool_failure(prune_reason)
            pruned_count += 1
        result.append(item)
    post_chars = _messages_chars(result) + tool_chars
    return result, {
        "request_level_prune_applied": True,
        "request_level_pruned_tool_message_count": pruned_count,
        "request_level_unavailable_failure_pruned_count": unavailable_count,
        "request_level_covered_failure_pruned_count": covered_count,
        "pre_prune_chars": pre_chars,
        "post_prune_chars": post_chars,
        "available_tool_names": sorted(available_tools),
        **_runtime_state_budget_metadata(task_state),
    }


def _tool_names_from_schemas(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for schema in tools:
        if not isinstance(schema, dict):
            continue
        function = schema.get("function") if isinstance(schema.get("function"), dict) else {}
        name = schema.get("name") or function.get("name")
        if name:
            names.add(str(name).rsplit(".", 1)[-1])
    return names


def _placeholder_tool_failure(reason: str) -> str:
    return json.dumps(
        {
            "success": False,
            "status": "failed",
            "pruned": True,
            "pruning_reason": reason,
            "message": "Stale or unavailable failed observation omitted from model context.",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _runtime_state_budget_metadata(task_state: Any) -> dict[str, Any]:
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    result: dict[str, Any] = {}
    for key in (
        "explore_file_read_satisfied",
        "explore_file_read_path",
        "explore_file_read_content_available",
        "explore_file_read_satisfied_note_added",
        "local_file_read_satisfied",
        "local_file_read_path",
        "local_file_read_content_available",
        "finalization_mode",
        "finalization_tools_disabled",
        "finalization_reason",
        "tool_required_effective",
        "execution_mode_effective",
    ):
        if key in metadata:
            result[key] = metadata.get(key)
    return result


def _explore_read_file_primary(task_state: Any, metadata: dict[str, Any]) -> bool:
    primary_capability = str(metadata.get("primary_capability") or metadata.get("tool_plan_primary_capability") or "").lower()
    primary_tool = str(metadata.get("primary_tool") or metadata.get("tool_plan_primary_tool") or "").rsplit(".", 1)[-1]
    if not primary_capability and not primary_tool:
        plan = getattr(task_state, "tool_plan", None)
        if isinstance(plan, dict):
            primary_capability = str(plan.get("primary_capability") or "").lower()
            primary_tool = str(plan.get("primary_tool") or "").rsplit(".", 1)[-1]
        profile = getattr(task_state, "task_profile", None)
        if not primary_capability and profile is not None:
            primary_capability = str(getattr(profile, "primary_capability", "") or "").lower()
        if not primary_tool and profile is not None:
            primary_tool = str(getattr(profile, "primary_tool", "") or "").rsplit(".", 1)[-1]
    return primary_capability == "file_read" or primary_tool == "read_file"


def _split_current_task_tail(groups: list[ContextGroup]) -> tuple[list[ContextGroup], list[ContextGroup]]:
    start = len(groups)
    for index in range(len(groups) - 1, -1, -1):
        if any(message.get("role") == "user" for message in groups[index].messages):
            start = index
            break
    return groups[:start], groups[start:]


def _trim_current_tail_for_satisfied_file_read(groups: list[ContextGroup]) -> tuple[list[ContextGroup], list[ContextGroup]]:
    if not groups:
        return groups, []
    kept: list[ContextGroup] = []
    demoted: list[ContextGroup] = []
    user_kept = False
    read_file_kept = False
    for group in groups:
        has_user = any(message.get("role") == "user" for message in group.messages)
        if has_user and not user_kept:
            kept.append(group)
            user_kept = True
            continue
        if group.kind == "tool_chain" and _group_has_successful_read_file(group) and not read_file_kept:
            kept.append(group)
            read_file_kept = True
            continue
        demoted.append(group)
    return kept, demoted


def _group_has_successful_read_file(group: ContextGroup) -> bool:
    for message in group.messages:
        if message.get("role") != "tool":
            continue
        payload = _parse_payload(str(message.get("content") or ""))
        if not payload:
            continue
        tool_name = str(message.get("name") or payload.get("tool") or "")
        if tool_name == "read_file" and _payload_success(payload):
            return True
    return False


def _explore_file_read_satisfied(task_state: Any) -> bool:
    metadata = getattr(task_state, "metadata", None)
    return isinstance(metadata, dict) and (
        metadata.get("local_file_read_satisfied") is True or metadata.get("explore_file_read_satisfied") is True
    )


def _tail_by_message_count(groups: list[ContextGroup], max_messages: int) -> list[ContextGroup]:
    selected: list[ContextGroup] = []
    count = 0
    for group in reversed(groups):
        size = len(group.messages)
        if selected and count + size > max_messages:
            break
        selected.append(group)
        count += size
    return list(reversed(selected))


def _clip_system_group(group: ContextGroup, limit: int) -> ContextGroup:
    message = dict(group.messages[0])
    content = str(message.get("content") or "")
    if _is_critical_system_message(message):
        return ContextGroup((message,), group.kind)
    if len(content) > limit:
        message["content"] = _truncate(content, limit)
    return ContextGroup((message,), group.kind)


def _is_critical_system_message(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    note_type = ""
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        note_type = str(metadata.get("note_type") or "")
    return bool(
        content.startswith("You are Horizon")
        or content.startswith("You are an AI")
        or "simple chat mode" in content
        or note_type in {"task_state", "tool_schema_scope", "runtime_state", "context_compact_summary"}
    )


def _insert_summary(messages: list[dict[str, Any]], summary: str) -> list[dict[str, Any]]:
    summary_message = {
        "role": "system",
        "content": summary,
        "metadata": {"note_type": "context_compact_summary"},
    }
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    return [*messages[:insert_at], summary_message, *messages[insert_at:]]


def _build_compact_summary(
    groups: list[ContextGroup],
    cfg: ContextBudgetConfig,
    *,
    lane: str,
    task_state: Any,
    all_groups: list[ContextGroup],
    extra_lines: list[str] | None = None,
    max_chars: int | None = None,
    summary_metadata: dict[str, Any] | None = None,
) -> str:
    success_targets = _success_targets(all_groups)
    users: list[str] = []
    assistants: list[str] = []
    tools: list[str] = []
    files: list[str] = []
    sources: list[str] = []
    failures: list[str] = []
    recovery_records: list[dict[str, Any]] = []
    for group in groups:
        for message in group.messages:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "user" and content:
                users.append(_one_line(content, 180))
            elif role == "assistant" and content:
                assistants.append(_one_line(content, 220))
            elif role == "tool":
                summary = _summarize_tool_message(message, success_targets=success_targets)
                if summary:
                    tools.append(summary)
                recovery = _tool_recovery_record(message)
                if recovery:
                    recovery_records.append(recovery)
                _collect_paths_and_sources(content, files, sources, failures, success_targets=success_targets)
    for attr in ("modified_files", "output_files"):
        for item in getattr(task_state, attr, []) or []:
            files.append(str(item))
    if not any((extra_lines, users, assistants, tools, files, sources, failures)):
        return ""
    lines = ["Context Compact Summary:", f"- runtime_lane: {lane or 'unknown'}"]
    lines.extend(extra_lines or [])
    if users:
        lines.append(f"- older_user_goals: {' | '.join(users[-5:])}")
    if assistants:
        lines.append(f"- older_assistant_conclusions: {' | '.join(assistants[-5:])}")
    if tools:
        lines.append(f"- older_tool_outcomes: {' | '.join(tools[-8:])}")
    if files:
        lines.append(f"- files_seen_or_changed: {' | '.join(_unique(files)[-12:])}")
    if sources:
        lines.append(f"- sources: {' | '.join(_unique(sources)[-8:])}")
    if failures:
        lines.append(f"- unresolved_or_recent_failures: {' | '.join(_unique(failures)[-5:])}")
    limit = max_chars or cfg.max_compact_summary_chars
    metadata = summary_metadata if isinstance(summary_metadata, dict) else {}
    metadata["reference_count"] = len(recovery_records)
    recovery_line = "- tool_recovery: " + json.dumps(recovery_records, ensure_ascii=False, separators=(",", ":")) if recovery_records else ""
    text = "\n".join([*lines, recovery_line] if recovery_line else lines)
    if len(text) <= limit:
        return text
    if recovery_records:
        task_id = str(getattr(task_state, "task_id", "") or "history")
        stored = ToolResultStore().store_json(recovery_records, task_id=task_id, call_id="history", kind="tool_manifest")
        metadata.update({"manifest_ref": stored.content_ref, "manifest_sha256": stored.sha256})
        manifest_lines = [
            f"- history_tool_manifest_ref: {stored.content_ref}",
            f"- manifest_sha256: {stored.sha256}",
            f"- history_tool_result_count: {len(recovery_records)}",
        ]
        available = max(200, limit - len("\n".join(manifest_lines)) - 1)
        return _truncate("\n".join(lines), available) + "\n" + "\n".join(manifest_lines)
    return _truncate(text, limit)


def _tool_recovery_record(message: dict[str, Any]) -> dict[str, Any]:
    payload = _parse_payload(str(message.get("content") or ""))
    if not isinstance(payload, dict):
        return {}
    if not payload.get("call_id") and not payload.get("provider_call_id"):
        payload["call_id"] = str(message.get("tool_call_id") or "")
    if not payload.get("tool") and not payload.get("tool_name"):
        payload["tool_name"] = str(message.get("name") or "")
    return build_tool_result_card(payload)


def _current_task_success_summary(groups: list[ContextGroup], *, lane: str) -> list[str]:
    if lane not in {"explore", "research"}:
        return []
    current_user = ""
    read_file_target = ""
    read_file_preview = ""
    for group in groups:
        for message in group.messages:
            if message.get("role") == "user":
                current_user = _one_line(str(message.get("content") or ""), 220)
            if message.get("role") != "tool":
                continue
            payload = _parse_payload(str(message.get("content") or ""))
            payload_tool = str(payload.get("tool") or "") if payload else ""
            if str(message.get("name") or payload_tool) != "read_file":
                continue
            if not payload or not _payload_success(payload):
                continue
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            read_file_target = _target_from_payload(payload)
            read_file_preview = _one_line(str(data.get("content") or data.get("text") or payload.get("content") or payload.get("text") or ""), 260)
    if not read_file_target:
        return []
    lines = [
        f"- current_task: {current_user or 'read and summarize the requested file'}",
        f"- current_successful_result: read_file successfully read {read_file_target}.",
        "- current_instruction: Use the read_file content as the authoritative result.",
        "- current_instruction: Do not report older covered read/search failures.",
    ]
    if read_file_preview:
        lines.append(f"- current_read_file_preview: {read_file_preview}")
    return lines


def _summarize_tool_message(message: dict[str, Any], *, success_targets: set[str]) -> str:
    name = str(message.get("name") or "tool")
    payload = _parse_payload(str(message.get("content") or ""))
    if payload is None:
        return _one_line(f"{name}: {message.get('content') or ''}", 220)
    if _is_pruned_or_covered_failure(payload, name, success_targets):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    success = _payload_success(payload)
    status = payload.get("status") or data.get("status") or ("success" if success else "failed")
    target = _target_from_payload(payload)
    if success and name == "read_file":
        preview = _one_line(str(data.get("text") or data.get("content") or ""), 220)
        return _one_line(f"{name}: status={status} path={target} preview={preview}", 360)
    if success and name == "sandbox_exec":
        stdout = _one_line(str(data.get("stdout") or ""), 180)
        return _one_line(f"{name}: status={status} command={target} exit_code={data.get('exit_code', data.get('returncode'))} stdout={stdout}", 360)
    if success:
        return _one_line(f"{name}: status={status} target={target}", 260)
    error = payload.get("error") or payload.get("error_code") or data.get("code") or payload.get("message") or ""
    return _one_line(f"{name}: status={status} target={target} error={error}", 260)


def _collect_paths_and_sources(
    content: str,
    files: list[str],
    sources: list[str],
    failures: list[str],
    *,
    success_targets: set[str],
) -> None:
    payload = _parse_payload(content)
    if payload is None:
        return
    tool_name = str(payload.get("tool") or "")
    if _is_pruned_or_covered_failure(payload, tool_name, success_targets):
        return
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    target = _target_from_payload(payload)
    if target and _looks_like_path(target):
        files.append(target)
    url = payload.get("url") or data.get("url")
    if url:
        sources.append(str(url))
    if payload.get("success") is False:
        failures.append(_one_line(str(payload.get("error") or payload.get("message") or payload.get("error_code") or ""), 180))


def _is_pruned_or_covered_failure(payload: dict[str, Any], tool_name: str, success_targets: set[str] | None = None) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reason = str(payload.get("pruning_reason") or data.get("pruning_reason") or "")
    message = str(payload.get("message") or payload.get("error") or "")
    target = _target_from_payload(payload)
    success_targets = success_targets or set()
    if payload.get("pruned") is True:
        return True
    if reason in PRUNED_FAILURE_REASONS:
        return True
    if any(text in message for text in ("Intermediate failed read omitted", "Failed observation omitted", "Repeated failed observation omitted")):
        return True
    if target and _target_covered(target, success_targets) and not _payload_success(payload):
        return True
    if success_targets and not _payload_success(payload) and tool_name == "read_file" and target and not _target_covered(target, success_targets):
        return True
    if tool_name in DOCUMENT_TOOLS and target.endswith(".py"):
        return True
    if tool_name == "search_text" and _looks_like_file_path(target):
        error_text = " ".join(str(item or "") for item in (payload.get("error"), payload.get("message"), data.get("error"), data.get("message")))
        if "不是目录" in error_text or "not a directory" in error_text.lower():
            return True
    return False


def _success_targets(groups: list[ContextGroup]) -> set[str]:
    targets: set[str] = set()
    successful_file_reads: set[str] = set()
    for group in groups:
        for message in group.messages:
            if message.get("role") != "tool":
                continue
            payload = _parse_payload(str(message.get("content") or ""))
            if not payload or not _payload_success(payload):
                continue
            target = _target_from_payload(payload)
            if target:
                targets.add(target)
            if str(message.get("name") or payload.get("tool") or "") == "read_file" and target:
                successful_file_reads.add(target)
    targets.update(successful_file_reads)
    return targets


def _drop_old_non_current_groups(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = group_protocol_messages(messages)
    system_groups = [group for group in groups if group.kind == "system"]
    non_system = [group for group in groups if group.kind not in {"system", "orphan_tool"}]
    _, current_tail = _split_current_task_tail(non_system)
    return _flatten_groups([*system_groups, *current_tail])


def _truncate_summary_messages(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata.get("note_type") == "context_compact_summary":
            item["content"] = _truncate(str(item.get("content") or ""), limit)
        result.append(item)
    return result


def _truncate_noncritical_system_notes(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "system" and not _is_critical_system_message(item):
            item["content"] = _truncate(str(item.get("content") or ""), limit)
        result.append(item)
    return result


def _prune_tool_outputs(messages: list[dict[str, Any]], limit: int, *, protected_limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    result: list[dict[str, Any]] = []
    pruned = 0
    success_targets = _success_targets(group_protocol_messages(messages))
    for message in messages:
        item = dict(message)
        if item.get("role") == "tool":
            content = str(item.get("content") or "")
            payload = _parse_payload(content)
            tool_name = str(item.get("name") or (payload or {}).get("tool") or "")
            if payload is not None and _is_pruned_or_covered_failure(payload, tool_name, success_targets):
                item["content"] = json.dumps(
                    {
                        "success": False,
                        "status": "failed",
                        "pruned": True,
                        "pruning_reason": "current_tail_covered_failure",
                        "message": "Covered or irrelevant failed observation omitted from model context.",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                pruned += 1
                result.append(item)
                continue
            tool_limit = protected_limit if protected_limit and _is_protected_tool_message(item) else limit
            compact = _compact_tool_content(content, tool_limit)
            if compact != content:
                pruned += 1
                item["content"] = compact
        result.append(item)
    return result, pruned


def _is_protected_tool_message(message: dict[str, Any]) -> bool:
    name = str(message.get("name") or "")
    payload = _parse_payload(str(message.get("content") or ""))
    return bool(payload and _payload_success(payload) and name in {"read_file", "sandbox_exec", "write_file", "replace_in_file"})


def _compact_tool_content(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    payload = _parse_payload(content)
    if payload is None:
        return _truncate(content, limit)
    compacted = _compact_value(payload, limit)
    return json.dumps(sanitize_unicode(compacted), ensure_ascii=False, separators=(",", ":"))


def _compact_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return _truncate(value, limit)
    if isinstance(value, list):
        return [_compact_value(item, limit) for item in value[:8]]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"provider_metadata", "_observation_pruning_state"}:
                continue
            if key in {"stdout", "stderr", "text", "content", "body", "html", "markdown"} and isinstance(item, str):
                result[key] = _truncate(item, limit)
            else:
                result[key] = _compact_value(item, limit)
        return result
    return value


def _parse_payload(content: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _target_from_payload(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in PATH_KEYS:
        value = payload.get(key) or data.get(key)
        if value:
            return str(value)
    return ""


def _payload_success(payload: dict[str, Any]) -> bool:
    return payload.get("success") is True or str(payload.get("status") or "").strip().lower() == "success"


def _target_covered(target: str, success_targets: set[str]) -> bool:
    if target in success_targets:
        return True
    target_base = target.rsplit("/", 1)[-1]
    return bool(target_base and any(item.rsplit("/", 1)[-1] == target_base for item in success_targets))


def _looks_like_path(value: str) -> bool:
    return bool(value and ("/" in value or "." in value))


def _looks_like_file_path(value: str) -> bool:
    tail = value.rsplit("/", 1)[-1]
    return bool("." in tail and not value.endswith("/"))


def _flatten_groups(groups: list[ContextGroup]) -> list[dict[str, Any]]:
    return [dict(message) for group in groups for message in group.messages]


def _groups_chars(groups: list[ContextGroup]) -> int:
    return sum(_group_chars(group) for group in groups)


def _group_chars(group: ContextGroup) -> int:
    return _messages_chars(list(group.messages))


def _decision(
    *,
    enabled: bool,
    action: str,
    reason: str,
    runtime_lane: str,
    original: int,
    final: int,
    original_count: int,
    final_count: int,
    tool_chars: int,
    compacted_count: int = 0,
    pruned_tool_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> ContextBudgetDecision:
    return ContextBudgetDecision(
        enabled=enabled,
        action=action,
        reason=reason,
        runtime_lane=runtime_lane,
        original_chars=original,
        final_chars=final,
        saved_chars=max(0, original - final),
        original_message_count=original_count,
        final_message_count=final_count,
        tool_schema_chars=tool_chars,
        compacted_message_count=compacted_count,
        pruned_tool_message_count=pruned_tool_count,
        metadata=metadata or {},
    )


def _messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        total += len(content) if isinstance(content, str) else _json_chars(content)
        if message.get("tool_calls"):
            total += _json_chars(message.get("tool_calls"))
    return total


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception:
        return len(str(value))


def _one_line(text: str, limit: int) -> str:
    return _truncate(" ".join(str(text or "").split()), limit)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
