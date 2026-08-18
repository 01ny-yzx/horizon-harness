"""Prune model-visible tool observations without changing execution facts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from core.observation_compaction import compact_observation_for_model
from core.structured_intent_access import structured_tool_plan
from core.unicode_safety import sanitize_unicode


MAX_TEXT_FIELD_CHARS = 1200
MAX_STDIO_CHARS = 1600
MAX_LIST_ITEMS = 8
LOCAL_FILE_READ_TOOLS = {"read_file", "list_files", "find_files", "get_project_tree", "search_text"}
DOCUMENT_CONTEXT_TOOLS = {
    "read_document",
    "load_document",
    "load_documents_from_directory",
    "list_documents",
    "find_documents",
    "list_chunks",
    "get_chunk",
    "search_document_chunks",
    "semantic_search_chunks",
    "hybrid_search_chunks",
}
BUILD_IMPORTANT_TOOLS = {"write_file", "replace_in_file", "sandbox_exec"}
RESEARCH_TOOLS = {
    "web_search",
    "fetch_url",
    "browser_extract_text",
    "browser_screenshot",
    "browser_list_links",
    "browser_click_and_extract",
    "rag_query",
}


@dataclass(frozen=True)
class ObservationPruningDecision:
    enabled: bool
    reason: str
    tool_name: str
    runtime_lane: str
    original_chars: int
    pruned_chars: int
    pruned: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


def prune_observation_for_model_context(
    observation_json: str,
    *,
    tool_name: str,
    runtime_lane: str,
    task_state: Any,
    tool_call_id: str = "",
) -> tuple[str, ObservationPruningDecision]:
    """Return model-context observation JSON and an audit decision."""

    original = str(observation_json or "")
    lane = str(runtime_lane or "").strip().lower()
    tool = _base_name(tool_name)
    try:
        payload = json.loads(original)
    except json.JSONDecodeError:
        pruned_payload = _placeholder(tool, "invalid_observation_json", "Observation JSON could not be parsed.")
        return _finish(task_state, original, pruned_payload, tool, lane, "invalid_observation_json", True, tool_call_id)

    payload = payload if isinstance(payload, dict) else {"success": True, "data": {"result": payload}}
    payload = _normalize_payload_data(payload)
    compact_payload = _compact_payload(payload, lane=lane, tool_name=tool)
    success = payload.get("success") is True or str(payload.get("status") or "").lower() == "success"
    target = _target_key(payload)
    failure_key = _failure_key(tool, target, payload)
    metadata = _pruning_metadata(task_state)
    reason = "kept_success_observation" if success else "kept_failure_observation"
    pruned = False
    result_payload = compact_payload

    if lane == "chat":
        result_payload = _placeholder(tool, "chat_lane_observation_placeholder", "Tool observation omitted from chat model context.")
        reason = "chat_lane_observation_placeholder"
        pruned = True
    elif lane == "explore" and _primary_is_file_read(task_state):
        if success and tool in LOCAL_FILE_READ_TOOLS:
            result_payload = compact_payload
            reason = "explore_file_read_success_compacted"
            _remember_success(metadata, tool, target)
        elif not success and tool in DOCUMENT_CONTEXT_TOOLS:
            result_payload = _placeholder(
                tool,
                "covered_failed_context_read",
                "Intermediate failed read omitted from model context because a file_read result is available.",
                payload=payload,
            )
            reason = "covered_failed_context_read"
            pruned = True
        elif not success and _has_success_for_target(metadata, target):
            result_payload = _placeholder(tool, "covered_failed_observation", "Failed observation omitted because a successful read exists.")
            reason = "covered_failed_observation"
            pruned = True
        elif not success and _seen_failure(metadata, failure_key):
            result_payload = _placeholder(tool, "duplicate_failed_observation", "Repeated failed observation omitted from model context.")
            reason = "duplicate_failed_observation"
            pruned = True
        elif success:
            _remember_success(metadata, tool, target)
            reason = "explore_success_compacted"
    elif lane == "build":
        if success and tool in BUILD_IMPORTANT_TOOLS:
            result_payload = compact_payload
            reason = "build_important_success_compacted"
            _remember_success(metadata, tool, target)
        elif not success and _seen_failure(metadata, failure_key):
            result_payload = _placeholder(tool, "duplicate_failed_observation", "Repeated failed observation omitted from model context.")
            reason = "duplicate_failed_observation"
            pruned = True
        elif success:
            _remember_success(metadata, tool, target)
            reason = "build_observation_compacted"
    elif lane == "research":
        if not success and _seen_failure(metadata, failure_key):
            result_payload = _placeholder(tool, "duplicate_failed_source_observation", "Repeated source-read failure omitted from model context.")
            reason = "duplicate_failed_source_observation"
            pruned = True
        elif success and tool in RESEARCH_TOOLS:
            result_payload = _research_payload(payload, tool)
            reason = "research_source_observation_compacted"
            _remember_success(metadata, tool, target)
        elif success:
            _remember_success(metadata, tool, target)
            reason = "research_observation_compacted"
    else:
        if not success and _seen_failure(metadata, failure_key):
            result_payload = _placeholder(tool, "duplicate_failed_observation", "Repeated failed observation omitted from model context.")
            reason = "duplicate_failed_observation"
            pruned = True
        elif success:
            _remember_success(metadata, tool, target)

    if not success:
        _remember_failure(metadata, failure_key)
    if result_payload != payload:
        pruned = True
    return _finish(task_state, original, result_payload, tool, lane, reason, pruned, tool_call_id)


def _finish(
    task_state: Any,
    original: str,
    payload: dict[str, Any],
    tool_name: str,
    runtime_lane: str,
    reason: str,
    pruned: bool,
    tool_call_id: str,
) -> tuple[str, ObservationPruningDecision]:
    model_json = json.dumps(sanitize_unicode(payload), ensure_ascii=False, separators=(",", ":"))
    decision = ObservationPruningDecision(
        enabled=True,
        reason=reason,
        tool_name=tool_name,
        runtime_lane=runtime_lane,
        original_chars=len(original),
        pruned_chars=len(model_json),
        pruned=pruned or len(model_json) < len(original),
        metadata={"tool_call_id": tool_call_id},
    )
    _record_pruning(task_state, decision)
    return model_json, decision


def _record_pruning(task_state: Any, decision: ObservationPruningDecision) -> None:
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        return
    if decision.pruned:
        metadata["observation_pruning_count"] = int(metadata.get("observation_pruning_count") or 0) + 1
        saved = max(0, decision.original_chars - decision.pruned_chars)
        metadata["observation_pruning_saved_chars"] = int(metadata.get("observation_pruning_saved_chars") or 0) + saved
    events = metadata.get("observation_pruning_events")
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "tool_name": decision.tool_name,
            "runtime_lane": decision.runtime_lane,
            "reason": decision.reason,
            "original_chars": decision.original_chars,
            "pruned_chars": decision.pruned_chars,
            "pruned": decision.pruned,
        }
    )
    metadata["observation_pruning_events"] = events[-20:]


def _compact_payload(payload: dict[str, Any], *, lane: str, tool_name: str) -> dict[str, Any]:
    payload = _normalize_payload_data(payload)
    if _is_compact_capsule(payload):
        return sanitize_unicode(payload)
    compacted = compact_observation_for_model(payload, runtime_lane=lane)
    model_summary = compacted.get("model_visible_summary") if isinstance(compacted, dict) else None
    if isinstance(model_summary, dict):
        return sanitize_unicode(model_summary)
    result = _compact_value(payload, text_limit=MAX_TEXT_FIELD_CHARS, stdio_limit=MAX_STDIO_CHARS)
    return result if isinstance(result, dict) else payload


def _is_compact_capsule(payload: dict[str, Any]) -> bool:
    return payload.get("observation_compacted") is True or payload.get("_already_compacted") is True


def _build_payload(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if tool_name != "sandbox_exec":
        return _compact_value(payload, text_limit=MAX_TEXT_FIELD_CHARS, stdio_limit=MAX_STDIO_CHARS)
    kept = {
        "success": payload.get("success") is True,
        "status": payload.get("status") or data.get("status") or ("success" if payload.get("success") is True else "failed"),
        "tool": payload.get("tool") or tool_name,
        "data": {
            "command": data.get("command") or "",
            "cwd": data.get("cwd") or "",
            "exit_code": data.get("exit_code", data.get("returncode")),
            "stdout": _truncate(str(data.get("stdout") or ""), MAX_STDIO_CHARS),
            "stderr": _truncate(str(data.get("stderr") or ""), MAX_STDIO_CHARS),
        },
    }
    if payload.get("error"):
        kept["error"] = payload.get("error")
    return sanitize_unicode(kept)


def _research_payload(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    compacted = _compact_value(payload, text_limit=1000, stdio_limit=MAX_STDIO_CHARS)
    if not isinstance(compacted, dict):
        return payload
    data = compacted.get("data")
    if isinstance(data, dict):
        for key in ("text", "content", "html", "markdown", "body"):
            if isinstance(data.get(key), str):
                data[key] = _truncate(data[key], 1000)
        for key in ("results", "matches", "sources"):
            if isinstance(data.get(key), list):
                data[key] = data[key][:MAX_LIST_ITEMS]
    return compacted


def _placeholder(tool_name: str, reason: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    source = payload or {}
    data = source.get("data") if isinstance(source.get("data"), dict) else {}
    result = {
        "success": False,
        "status": source.get("status") or "failed",
        "tool": source.get("tool") or tool_name,
        "message": message,
        "pruned": True,
        "pruning_reason": reason,
    }
    target = _target_key(source)
    if target:
        result["target"] = target
    error_code = source.get("error_code") or data.get("code") or data.get("error_code")
    if error_code:
        result["error_code"] = str(error_code)
    return sanitize_unicode(result)


def _read_file_success_payload(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    payload = _normalize_payload_data(payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    content = str(data.get("content") or data.get("text") or payload.get("content") or payload.get("text") or "")
    kept = {
        "success": True,
        "status": payload.get("status") or "success",
        "tool": payload.get("tool") or tool_name,
        "data": {
            "path": data.get("path") or payload.get("path") or payload.get("target") or "",
            "content": _truncate(content, MAX_TEXT_FIELD_CHARS),
            "text": _truncate(content, MAX_TEXT_FIELD_CHARS),
        },
    }
    return sanitize_unicode(kept)


def _normalize_payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    raw_data = payload.get("data")
    if isinstance(raw_data, str):
        normalized = dict(payload)
        normalized["data"] = {"content": raw_data, "text": raw_data}
        return normalized
    return payload


def _compact_value(value: Any, *, text_limit: int, stdio_limit: int) -> Any:
    if isinstance(value, str):
        return _truncate(value, text_limit)
    if isinstance(value, list):
        compacted = [_compact_value(item, text_limit=text_limit, stdio_limit=stdio_limit) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            compacted.append({"truncated": f"{len(value) - MAX_LIST_ITEMS} more items omitted"})
        return compacted
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"stdout", "stderr"} and isinstance(item, str):
                result[key] = _truncate(item, stdio_limit)
            elif key in {"text", "content", "body", "html", "markdown"} and isinstance(item, str):
                result[key] = _truncate(item, text_limit)
            else:
                result[key] = _compact_value(item, text_limit=text_limit, stdio_limit=stdio_limit)
        return result
    return value


def _pruning_metadata(task_state: Any) -> dict[str, Any]:
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    state = metadata.get("_observation_pruning_state")
    if not isinstance(state, dict):
        state = {"success_targets": [], "failure_keys": []}
        metadata["_observation_pruning_state"] = state
    return state


def _remember_success(metadata: dict[str, Any], tool_name: str, target: str) -> None:
    if not target:
        return
    values = metadata.get("success_targets")
    values = values if isinstance(values, list) else []
    entry = f"{tool_name}:{target}"
    generic = f"*:{target}"
    metadata["success_targets"] = [*values, entry, generic][-50:]


def _has_success_for_target(metadata: dict[str, Any], target: str) -> bool:
    if not target:
        return False
    values = metadata.get("success_targets")
    return isinstance(values, list) and f"*:{target}" in values


def _remember_failure(metadata: dict[str, Any], failure_key: str) -> None:
    if not failure_key:
        return
    values = metadata.get("failure_keys")
    values = values if isinstance(values, list) else []
    metadata["failure_keys"] = [*values, failure_key][-80:]


def _seen_failure(metadata: dict[str, Any], failure_key: str) -> bool:
    values = metadata.get("failure_keys")
    return bool(failure_key and isinstance(values, list) and failure_key in values)


def _failure_key(tool_name: str, target: str, payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    error = str(payload.get("error_code") or data.get("code") or data.get("error_code") or payload.get("error") or "")
    return "|".join([tool_name, target, error])


def _target_key(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("target", "path", "url", "command", "cwd"):
        value = payload.get(key) or data.get(key)
        if value:
            return str(value)
    return ""


def _primary_is_file_read(task_state: Any) -> bool:
    plan = structured_tool_plan(task_state)
    primary_capability = str(plan.get("primary_capability") or "").strip().lower()
    primary_tool = _base_name(plan.get("primary_tool"))
    return primary_capability == "file_read" or primary_tool == "read_file"


def _base_name(value: Any) -> str:
    return str(value or "").strip().lower().rsplit(".", 1)[-1]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n... [truncated {omitted} chars]"
