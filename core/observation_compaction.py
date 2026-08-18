"""Structured model-visible compaction for tool observations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from config.settings import settings
from core.document_result_compaction import compact_document_result
from core.path_grounding import compact_path_grounding
from core.tool_result_store import resolve_tool_artifact_metadata
from core.unicode_safety import sanitize_unicode


DEFAULT_PREVIEW_CHARS = 800
LIST_ITEM_LIMIT = 20
STDIO_PREVIEW_CHARS = 500
STDIO_TAIL_CHARS = 500


@dataclass(frozen=True)
class ObservationCompactResult:
    tool_name: str
    success: bool
    status: str
    kind: str
    original_chars: int
    compacted_chars: int
    saved_chars: int
    strategy: str
    content_ref: str
    model_visible_summary: dict[str, Any]
    trace_summary: dict[str, Any]
    dropped_fields: list[str] = field(default_factory=list)
    preview_chars: int = 0
    has_large_content: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


def compact_observation_for_model(
    observation: Any,
    *,
    runtime_lane: str = "",
    current_step: dict[str, Any] | None = None,
    max_preview_chars: int | None = None,
) -> dict[str, Any]:
    preview_chars = max(
        200,
        int(max_preview_chars or getattr(settings, "tool_result_preview_chars", DEFAULT_PREVIEW_CHARS) or DEFAULT_PREVIEW_CHARS),
    )
    payload = _payload_from_observation(observation)
    tool_name = _tool_name(payload, observation)
    return compact_tool_result_for_model(
        tool_name,
        payload.get("data"),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        success=payload.get("success") is True,
        error=str(payload.get("error") or ""),
        runtime_lane=runtime_lane,
        current_step=current_step,
        max_preview_chars=preview_chars,
        payload=payload,
    )


def compact_tool_result_for_model(
    tool_name: str,
    data: Any,
    metadata: dict[str, Any] | None = None,
    success: bool = True,
    error: str = "",
    runtime_lane: str = "",
    current_step: dict[str, Any] | None = None,
    max_preview_chars: int = DEFAULT_PREVIEW_CHARS,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {"success": success, "tool": tool_name, "data": data, "metadata": metadata or {}, "error": error}
    payload = _validated_source_payload(payload, tool_name)
    payload = _validated_artifact_payload(payload)
    metadata = metadata if isinstance(metadata, dict) else {}
    original_chars = estimate_observation_chars(payload)
    tool = _base_tool(tool_name or _tool_name(payload, None))
    summary: dict[str, Any]
    dropped: list[str] = []
    content_ref = ""
    strategy = "generic_compact"
    reason = "generic observation compacted"
    preview_chars = max_preview_chars

    if tool == "read_file":
        summary, content_ref, dropped, strategy, reason = _compact_read_file(payload, metadata, max_preview_chars)
    elif tool == "read_document":
        summary, content_ref, dropped, strategy, reason = _compact_read_document(payload, metadata, max_preview_chars)
    elif tool in {"load_document", "load_documents_from_directory"}:
        summary, content_ref, dropped, strategy, reason = _compact_document_load(payload, metadata, tool)
    elif tool == "list_files":
        summary, content_ref, dropped, strategy, reason = _compact_list_files(payload, metadata)
    elif tool in {"write_file", "replace_in_file"}:
        summary, content_ref, dropped, strategy, reason = _compact_file_write(payload, metadata, tool)
    elif tool == "sandbox_exec":
        summary, content_ref, dropped, strategy, reason = _compact_sandbox_exec(payload, metadata)
    else:
        summary, content_ref, dropped, strategy, reason = _compact_generic(payload, metadata, max_preview_chars)

    summary.setdefault("tool_name", tool or tool_name)
    summary.setdefault("success", bool(success or payload.get("success") is True))
    summary.setdefault("status", str(payload.get("status") or ("success" if summary.get("success") else "failed")))
    summary.setdefault("kind", str(payload.get("kind") or ""))
    summary.setdefault("runtime_lane", str(runtime_lane or ""))
    if current_step:
        summary["current_step"] = _compact_current_step(current_step)
    if error and not summary.get("error"):
        summary["error"] = _preview(error, max_preview_chars)
    summary["observation_compacted"] = True
    summary["_already_compacted"] = True

    compacted_chars = estimate_observation_chars(summary)
    result = ObservationCompactResult(
        tool_name=str(summary.get("tool_name") or tool_name),
        success=bool(summary.get("success")),
        status=str(summary.get("status") or ""),
        kind=str(summary.get("kind") or ""),
        original_chars=original_chars,
        compacted_chars=compacted_chars,
        saved_chars=max(0, original_chars - compacted_chars),
        strategy=strategy,
        content_ref=content_ref,
        model_visible_summary=sanitize_unicode(summary),
        trace_summary=sanitize_unicode(
            {
                "tool_name": str(summary.get("tool_name") or tool_name),
                "strategy": strategy,
                "original_chars": original_chars,
                "compacted_chars": compacted_chars,
                "saved_chars": max(0, original_chars - compacted_chars),
                "has_large_content": original_chars > compacted_chars,
                "content_ref": content_ref,
                "runtime_lane": str(runtime_lane or ""),
                "reason": reason,
            }
        ),
        dropped_fields=dropped,
        preview_chars=preview_chars,
        has_large_content=original_chars > compacted_chars,
        reason=reason,
    )
    compacted = result.to_dict()
    compacted["model_observation_json"] = json.dumps(compacted["model_visible_summary"], ensure_ascii=False, separators=(",", ":"))
    return compacted


def observation_compaction_summary(compacted: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(compacted, dict):
        return {}
    return sanitize_unicode(dict(compacted.get("trace_summary") or {}))


def build_tool_result_card(observation: Any, *, max_preview_chars: int | None = None) -> dict[str, Any]:
    """Build the single model-visible result card used by every compaction path."""

    payload = _payload_from_observation(observation)
    if payload.get("_already_compacted") is True or payload.get("observation_compacted") is True:
        return sanitize_unicode(payload)
    compacted = compact_observation_for_model(observation, max_preview_chars=max_preview_chars)
    return sanitize_unicode(dict(compacted.get("model_visible_summary") or {}))


def estimate_observation_chars(value: Any) -> int:
    try:
        return len(json.dumps(sanitize_unicode(value), ensure_ascii=False, default=str))
    except TypeError:
        return len(str(value))


def _compact_read_file(payload: dict[str, Any], metadata: dict[str, Any], max_preview_chars: int) -> tuple[dict[str, Any], str, list[str], str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not _payload_completed_success(payload):
        summary = _base_summary(payload, metadata)
        summary.update({"tool_name": "read_file", "requested_path": _requested_path(payload, metadata)})
        return summary, "", ["data.result", "data.content", "data.text"], "read_file_failure", "failed read body omitted"
    text = _first_text(payload)
    path = _path(payload, metadata)
    content_ref = _real_content_ref(payload, data)
    source_ref = str(payload.get("source_ref") or data.get("source_ref") or "")
    summary = _base_summary(payload, metadata)
    summary.update(
        {
            "tool_name": "read_file",
            "path": path,
            "chars": len(text),
            "preview": _preview(text, max_preview_chars),
            "truncated": len(text) > max_preview_chars,
            "content_ref": content_ref,
            **_source_metadata(payload, data),
            **_content_metadata(payload, data),
        }
    )
    return summary, content_ref, ["data.content", "data.text"], "read_file_preview_ref", "read_file content compacted to preview and ref"


def _compact_read_document(payload: dict[str, Any], metadata: dict[str, Any], max_preview_chars: int) -> tuple[dict[str, Any], str, list[str], str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not _payload_completed_success(payload):
        summary = _base_summary(payload, metadata)
        summary.update({"tool_name": "read_document", "requested_path": _requested_path(payload, metadata)})
        return summary, "", ["data.result", "data.content", "data.text"], "read_document_failure", "failed read body omitted"
    result = _result_value(payload)
    if isinstance(result, dict) and result.get("compacted") is True and _has_canonical_document_tables(result):
        visible = result
        projection_compacted = True
        projection_omitted = tuple(result.get("omitted_fields") or ())
    else:
        document = compact_document_result(
            result,
            max_preview_chars=max_preview_chars,
            max_table_rows=8,
            max_table_columns=12,
            max_tables=5,
        )
        visible = document.compacted_result
        projection_compacted = document.was_compacted
        projection_omitted = document.omitted_fields
    result_mapping = visible if isinstance(visible, dict) else {}
    text = str(result_mapping.get("excerpt") or result_mapping.get("text") or result_mapping.get("content") or result_mapping.get("markdown") or (visible if isinstance(visible, str) else ""))
    path = _path(payload, metadata)
    content_ref = _real_content_ref(payload, data)
    summary = _base_summary(payload, metadata)
    summary.update(
        {
            "tool_name": "read_document",
            "path": path,
            "filename": data.get("filename") or data.get("title") or "",
            "chars": len(text),
            "pages": data.get("pages") or data.get("page_count"),
            "table_count": result_mapping.get("table_count") or len(result_mapping.get("tables") or []),
            "total_sheet_count": result_mapping.get("total_sheet_count"),
            "parsed_sheet_count": result_mapping.get("parsed_sheet_count"),
            "visible_table_count": result_mapping.get("visible_table_count"),
            "tables": sanitize_unicode(result_mapping.get("tables") or []),
            "reader_omitted_tables": int(result_mapping.get("reader_omitted_tables") or 0),
            "projection_omitted_tables": int(result_mapping.get("projection_omitted_tables") or 0),
            "omitted_tables": int(result_mapping.get("omitted_tables") or 0),
            "excerpt": _preview(text, max_preview_chars),
            "content_ref": content_ref,
            "reader_truncated": bool(result_mapping.get("reader_truncated")),
            "truncated": bool(result_mapping.get("truncated") or projection_compacted or len(text) > max_preview_chars),
            "compacted": bool(result_mapping.get("compacted") or projection_compacted or data.get("compacted")),
            "omitted_fields": list(result_mapping.get("omitted_fields") or projection_omitted),
            **_source_metadata(payload, data),
            **_content_metadata(payload, data),
        }
    )
    return summary, content_ref, ["data.text", "data.content", "data.markdown"], "read_document_excerpt_ref", "document body compacted to excerpt and ref"


def _has_canonical_document_tables(result: dict[str, Any]) -> bool:
    tables = result.get("tables")
    if not isinstance(tables, list):
        return not any(result.get(key) for key in ("table_count", "total_sheet_count", "parsed_sheet_count"))
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("preview_rows"), list):
            return False
        if any(key in table for key in ("rows", "data", "values")):
            return False
    return True


def _compact_list_files(payload: dict[str, Any], metadata: dict[str, Any]) -> tuple[dict[str, Any], str, list[str], str, str]:
    result = _result_value(payload)
    if isinstance(result, dict):
        items = result.get("items") or result.get("files") or result.get("entries") or []
    else:
        items = result
    items = items if isinstance(items, list) else []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    content_ref = _real_content_ref(payload, data)
    should_compact = len(items) > LIST_ITEM_LIMIT and bool(content_ref)
    visible_items = items[:LIST_ITEM_LIMIT] if should_compact else items
    summary = _base_summary(payload, metadata)
    summary.update(
        {
            "tool_name": "list_files",
            "path": _path(payload, metadata),
            "item_count": len(items),
            "items": sanitize_unicode(visible_items),
            "omitted_count": max(0, len(items) - len(visible_items)),
            "content_ref": content_ref,
            **_content_metadata(payload, data),
        }
    )
    dropped = ["data.result.items"] if should_compact else []
    return summary, content_ref, dropped, "list_files_head", "directory listing projected with recoverable reference"


def _compact_document_load(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    tool: str,
) -> tuple[dict[str, Any], str, list[str], str, str]:
    """Keep document-store completion evidence while dropping parsed body text."""

    summary = _base_summary(payload, metadata)
    result = _result_value(payload)
    data = result if isinstance(result, dict) else {}
    for key in (
        "status",
        "document_id",
        "path",
        "source_path",
        "file_name",
        "store_status",
        "document_stored",
        "chunks_stored",
        "chunk_count",
        "loaded_count",
        "failed_count",
        "skipped_count",
        "total_chunks_created",
    ):
        if key in data:
            summary[key] = sanitize_unicode(data.get(key))
    for key in ("documents", "failures", "skipped"):
        values = data.get(key)
        if isinstance(values, list):
            summary[key] = sanitize_unicode(values[:20])
    dropped = [
        key
        for key in ("text", "text_preview", "summary", "headings", "content", "body")
        if key in data
    ]
    summary["compacted"] = bool(dropped)
    return summary, "", dropped, "document_load_evidence", "document load evidence preserved"


def _compact_file_write(payload: dict[str, Any], metadata: dict[str, Any], tool: str) -> tuple[dict[str, Any], str, list[str], str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    result = _result_value(payload)
    result_mapping = result if isinstance(result, dict) else {}
    path = str(data.get("path") or data.get("safe_path") or payload.get("path") or "")
    summary = _base_summary(payload, metadata)
    summary.update(
        {
            "tool_name": tool,
            "path": path,
            "output_path": path,
            "bytes": data.get("bytes") or data.get("bytes_written"),
            "chars_written": data.get("chars_written"),
            "collision_resolved": bool(data.get("collision_resolved")),
        }
    )
    content_ref = _real_content_ref(payload, data)
    summary.update(_content_metadata(payload, data))
    summary.update(_source_metadata(payload, data))
    summary["content_ref"] = content_ref
    source_ref = str(payload.get("source_ref") or data.get("source_ref") or "")
    if not content_ref and not source_ref:
        summary["result"] = sanitize_unicode(result)
    dropped = ["data.result.content", "data.result.preview", "data.result.content_preview"] if (content_ref or source_ref) and any(key in result_mapping for key in ("content", "preview", "content_preview")) else []
    return summary, content_ref, dropped, f"{tool}_evidence", "file write result projected with output reference"


def _compact_sandbox_exec(payload: dict[str, Any], metadata: dict[str, Any]) -> tuple[dict[str, Any], str, list[str], str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    stdout = str(data.get("stdout") or payload.get("stdout") or "")
    stderr = str(data.get("stderr") or payload.get("stderr") or "")
    command = str(data.get("command") or payload.get("command") or "")
    aggregate_ref = str(payload.get("content_ref") or data.get("content_ref") or "")
    summary = _base_summary(payload, metadata)
    summary.update(
        {
            "tool_name": "sandbox_exec",
            "command": _preview(command, 300),
            "cwd": data.get("cwd") or payload.get("cwd") or "",
            "host_resolved_cwd": data.get("host_resolved_cwd") or data.get("cwd") or payload.get("cwd") or "",
            "project_root": data.get("project_root") or "",
            "sandbox_dir": data.get("sandbox_dir") or "",
            "logical_root": data.get("logical_root") or "",
            "path_kind": data.get("path_kind") or "",
            "path_grounding": data.get("path_grounding") or {},
            "mode": "local_host",
            "exit_code": data.get("exit_code", data.get("returncode", payload.get("exit_code"))),
            "returncode": data.get("returncode", data.get("exit_code", payload.get("exit_code"))),
            "stdout_preview": _preview(stdout, STDIO_PREVIEW_CHARS) if data.get("stdout_ref") else stdout,
            "stdout_tail": _tail(stdout, STDIO_TAIL_CHARS) if data.get("stdout_ref") else stdout,
            "stderr_preview": _preview(stderr, STDIO_PREVIEW_CHARS) if data.get("stderr_ref") else stderr,
            "stderr_tail": _tail(stderr, STDIO_TAIL_CHARS) if data.get("stderr_ref") else stderr,
            "stdout_ref": data.get("stdout_ref") or "",
            "stderr_ref": data.get("stderr_ref") or "",
            "stdout_chars": data.get("stdout_chars", len(stdout)),
            "stderr_chars": data.get("stderr_chars", len(stderr)),
            "stdout_bytes": data.get("stdout_bytes", len(stdout.encode("utf-8"))),
            "stderr_bytes": data.get("stderr_bytes", len(stderr.encode("utf-8"))),
            "stdout_sha256": data.get("stdout_sha256") or "",
            "stderr_sha256": data.get("stderr_sha256") or "",
            "stdout_externalized": bool(data.get("stdout_externalized") or data.get("stdout_ref")),
            "stderr_externalized": bool(data.get("stderr_externalized") or data.get("stderr_ref")),
            "content_ref": aggregate_ref,
            **_content_metadata(payload, data),
            "timed_out": str(data.get("code") or payload.get("error_code") or "") == "timeout",
            "error_code": payload.get("error_code") or data.get("error_code") or data.get("code") or "",
            "compacted": True,
        }
    )
    refs = [str(value) for value in (data.get("stdout_ref"), data.get("stderr_ref")) if value]
    if aggregate_ref and aggregate_ref not in refs:
        refs.append(aggregate_ref)
    return summary, aggregate_ref, ["data.stdout", "data.stderr"] if refs else [], "sandbox_exec_stdio_preview_tail", "sandbox streams projected independently"


def _compact_generic(payload: dict[str, Any], metadata: dict[str, Any], max_preview_chars: int) -> tuple[dict[str, Any], str, list[str], str, str]:
    summary = _base_summary(payload, metadata)
    data_mapping = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data = _result_value(payload)
    content_ref = _real_content_ref(payload, data_mapping)
    omitted_paths: list[str] = []
    if isinstance(data, dict):
        if estimate_observation_chars(data) <= max_preview_chars or not content_ref:
            summary["result"] = sanitize_unicode(data)
        else:
            compacted_data, omitted_paths = _compact_business_value(data, max_preview_chars=max_preview_chars)
            summary["result"] = compacted_data
    elif isinstance(data, str):
        summary["result"] = data if len(data) <= max_preview_chars or not content_ref else {"preview": _preview(data, max_preview_chars), "chars": len(data)}
    elif isinstance(data, list):
        if estimate_observation_chars(data) <= max_preview_chars or not content_ref:
            summary["result"] = sanitize_unicode(data)
        else:
            compacted_data, omitted_paths = _compact_business_value(data, max_preview_chars=max_preview_chars)
            summary["result"] = compacted_data
    elif data is None or isinstance(data, (bool, int, float)):
        summary["result"] = data
    summary.update(_content_metadata(payload, data_mapping))
    if content_ref:
        summary["content_ref"] = content_ref
    if omitted_paths:
        summary["omitted_paths"] = omitted_paths
    return summary, content_ref, omitted_paths, "generic_compact", "generic observation compacted"


def _base_summary(payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    result = data.get("result") if "result" in data else data
    result_mapping = result if isinstance(result, dict) else {}
    grounding = data.get("path_grounding") if isinstance(data.get("path_grounding"), dict) else metadata.get("path_grounding")
    summary = {
        "observation_id": str(payload.get("observation_id") or ""),
        "call_id": str(payload.get("call_id") or payload.get("tool_call_id") or ""),
        "provider_call_id": str(payload.get("provider_call_id") or ""),
        "tool_name": _tool_name(payload, None),
        "success": payload.get("success") is True,
        "status": str(payload.get("status") or data.get("status") or ("success" if payload.get("success") is True else "failed")),
        "kind": str(payload.get("kind") or data.get("kind") or ""),
    }
    if payload.get("error") or data.get("message"):
        summary["error"] = _preview(str(payload.get("error") or data.get("message") or ""), DEFAULT_PREVIEW_CHARS)
    if payload.get("error_code") or data.get("error_code") or data.get("code"):
        summary["error_code"] = str(payload.get("error_code") or data.get("error_code") or data.get("code") or "")
    if payload.get("recoverable") is True or data.get("recoverable") is True or result_mapping.get("recoverable") is True:
        summary["recoverable"] = True
    recovery_reason = payload.get("recovery_reason") or data.get("recovery_reason") or result_mapping.get("recovery_reason")
    if recovery_reason:
        summary["recovery_reason"] = str(recovery_reason)
    if payload.get("policy_code") or data.get("policy_code"):
        summary["policy_code"] = str(payload.get("policy_code") or data.get("policy_code") or "")
    path = payload.get("path") or data.get("path") or data.get("output_path") or result_mapping.get("path") or result_mapping.get("output_path")
    url = payload.get("url") or data.get("url") or result_mapping.get("url")
    exit_code = payload.get("exit_code", data.get("exit_code", data.get("returncode", result_mapping.get("exit_code", result_mapping.get("returncode")))))
    if path:
        summary["path"] = str(path)
    if url:
        summary["url"] = str(url)
    if exit_code is not None:
        summary["exit_code"] = exit_code
    if isinstance(grounding, dict):
        summary["path_grounding"] = compact_path_grounding(grounding)
    return summary


def _compact_business_value(value: Any, *, max_preview_chars: int, path: str = "data", depth: int = 0) -> tuple[Any, list[str]]:
    if value is None or isinstance(value, (bool, int, float)):
        return value, []
    if isinstance(value, str):
        if len(value) <= max_preview_chars:
            return value, []
        return {"preview": _preview(value, max_preview_chars), "chars": len(value)}, [path]
    if depth >= 4:
        return {"omitted": True, "type": type(value).__name__}, [path]
    if isinstance(value, list):
        if len(value) <= LIST_ITEM_LIMIT and estimate_observation_chars(value) <= max_preview_chars:
            return sanitize_unicode(value), []
        selected = value[:5] + (value[-3:] if len(value) > 8 else [])
        compacted: list[Any] = []
        omitted: list[str] = [path]
        for index, item in enumerate(selected):
            child, child_omitted = _compact_business_value(item, max_preview_chars=max_preview_chars, path=f"{path}[{index}]", depth=depth + 1)
            compacted.append(child)
            omitted.extend(child_omitted)
        return {"items": compacted, "item_count": len(value), "omitted_count": max(0, len(value) - len(selected))}, _unique_strings(omitted)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        omitted: list[str] = []
        for key, item in value.items():
            child_path = f"{path}.{key}"
            child, child_omitted = _compact_business_value(item, max_preview_chars=max_preview_chars, path=child_path, depth=depth + 1)
            candidate_chars = estimate_observation_chars({**result, str(key): child})
            if candidate_chars <= max_preview_chars:
                result[str(key)] = child
            else:
                omitted.append(child_path)
            omitted.extend(child_omitted)
        if omitted:
            result["omitted_fields"] = _unique_strings(item.rsplit(".", 1)[-1] for item in omitted)
        return result, _unique_strings(omitted)
    return str(value), []


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if text and text not in result:
            result.append(text)
    return result


def _payload_from_observation(observation: Any) -> dict[str, Any]:
    if isinstance(observation, dict):
        return dict(observation)
    data = getattr(observation, "data", None)
    data = dict(data) if isinstance(data, dict) else {}
    output_text = str(getattr(observation, "output_text", "") or "")
    if output_text:
        data.setdefault("content", output_text)
        data.setdefault("text", output_text)
    metadata = getattr(observation, "metadata", None)
    output_path = str(getattr(observation, "output_path", "") or "")
    if output_path:
        data.setdefault("path", output_path)
    return {
        "observation_id": getattr(observation, "observation_id", ""),
        "call_id": getattr(observation, "call_id", ""),
        "provider_call_id": getattr(observation, "provider_call_id", ""),
        "tool": getattr(observation, "tool_name", ""),
        "success": bool(getattr(observation, "success", False)),
        "status": getattr(observation, "status", ""),
        "kind": getattr(observation, "kind", ""),
        "error": getattr(observation, "error", ""),
        "error_code": getattr(observation, "error_code", ""),
        "recoverable": bool(getattr(observation, "recoverable", False)),
        "recovery_reason": getattr(observation, "recovery_reason", ""),
        "policy_code": getattr(observation, "policy_code", ""),
        "source_ref": getattr(observation, "source_ref", ""),
        "source_chars": getattr(observation, "source_chars", None),
        "source_bytes": getattr(observation, "source_bytes", 0),
        "source_sha256": getattr(observation, "source_sha256", ""),
        "source_kind": getattr(observation, "source_kind", ""),
        "source_mime": getattr(observation, "source_mime", ""),
        "source_encoding": getattr(observation, "source_encoding", None),
        "source_is_text": getattr(observation, "source_is_text", False),
        "preview_chars": getattr(observation, "preview_chars", 0),
        "output_text_chars": getattr(observation, "output_text_chars", 0),
        "data": data,
        "metadata": metadata if isinstance(metadata, dict) else {},
        "stdout": getattr(observation, "stdout", ""),
        "stderr": getattr(observation, "stderr", ""),
        "exit_code": getattr(observation, "exit_code", None),
        "path": output_path,
        "content_ref": getattr(observation, "content_ref", ""),
        "content_chars": getattr(observation, "content_chars", 0),
        "content_bytes": getattr(observation, "content_bytes", 0),
        "content_sha256": getattr(observation, "content_sha256", ""),
        "content_externalized": bool(getattr(observation, "content_externalized", False)),
        "compacted": bool(getattr(observation, "compacted", False)),
    }


def _tool_name(payload: dict[str, Any], observation: Any) -> str:
    return str(payload.get("tool") or payload.get("tool_name") or getattr(observation, "tool_name", "") or "")


def _base_tool(tool_name: str) -> str:
    return str(tool_name or "").split(".")[-1]


def _first_text(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_data = payload.get("data")
    result = data.get("result") if "result" in data else raw_data
    result_mapping = result if isinstance(result, dict) else {}
    for value in (
        result if isinstance(result, str) else "",
        result_mapping.get("content"),
        result_mapping.get("text"),
        data.get("content"),
        data.get("text"),
        payload.get("content"),
        payload.get("text"),
    ):
        if isinstance(value, str) and value:
            return value
    return ""


def _result_value(payload: dict[str, Any]) -> Any:
    data = payload.get("data")
    if isinstance(data, dict) and "result" in data:
        return data.get("result")
    return data


def _path(payload: dict[str, Any], metadata: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    args = metadata.get("tool_arguments") if isinstance(metadata.get("tool_arguments"), dict) else {}
    return str(data.get("path") or payload.get("path") or payload.get("output_path") or args.get("path") or "")


def _real_content_ref(payload: dict[str, Any], data: dict[str, Any]) -> str:
    if not _payload_completed_success(payload):
        return ""
    explicit = str(
        payload.get("content_ref")
        or data.get("content_ref")
        or data.get("stdout_ref")
        or data.get("stderr_ref")
        or ""
    )
    if explicit:
        return explicit
    return ""


def _payload_completed_success(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    if status in {"failed", "blocked", "error", "rejected", "denied", "cancelled"}:
        return False
    return payload.get("success") is True or status in {"success", "completed"}


def _validated_artifact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    data = dict(result.get("data") or {}) if isinstance(result.get("data"), dict) else result.get("data")
    mapping = data if isinstance(data, dict) else {}
    tool = _base_tool(_tool_name(result, None))
    if tool in {"read_file", "read_document"} and not _payload_completed_success(result):
        for prefix in ("content", "stdout", "stderr"):
            for field in ("ref", "chars", "bytes", "sha256", "externalized"):
                result.pop(f"{prefix}_{field}", None)
                mapping.pop(f"{prefix}_{field}", None)
        if isinstance(data, dict):
            result["data"] = mapping
        return result
    entries = (
        ("content", result.get("content_ref") or mapping.get("content_ref")),
        ("stdout", mapping.get("stdout_ref")),
        ("stderr", mapping.get("stderr_ref")),
    )
    for name, ref in entries:
        if not ref:
            continue
        prefix = "content" if name == "content" else name
        validated = resolve_tool_artifact_metadata(
            str(ref),
            str(result.get("content_sha256") or mapping.get("content_sha256") or "") if name == "content" else str(mapping.get(f"{name}_sha256") or ""),
            _optional_int(result.get("content_chars") if "content_chars" in result else mapping.get("content_chars")) if name == "content" else _optional_int(mapping.get(f"{name}_chars")),
            _optional_int(result.get("content_bytes") if "content_bytes" in result else mapping.get("content_bytes")) if name == "content" else _optional_int(mapping.get(f"{name}_bytes")),
        )
        if not validated.valid:
            for field in ("ref", "chars", "bytes", "sha256", "externalized"):
                mapping.pop(f"{prefix}_{field}", None)
                if name == "content":
                    result.pop(f"content_{field}", None)
            result.update({"success": False, "status": "error", "error_code": validated.error_code})
            mapping["artifact_error_code"] = validated.error_code
            continue
        values = {
            f"{prefix}_ref": validated.ref,
            f"{prefix}_chars": validated.chars,
            f"{prefix}_bytes": validated.bytes,
            f"{prefix}_sha256": validated.sha256,
        }
        mapping.update(values)
        if name == "content":
            result.update(values)
    if isinstance(data, dict):
        result["data"] = mapping
    return result


def _validated_source_payload(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Project saved Source metadata without re-reading mutable user files."""

    result = dict(payload)
    data = dict(result.get("data") or {}) if isinstance(result.get("data"), dict) else result.get("data")
    mapping = data if isinstance(data, dict) else {}
    if not _payload_completed_success(result):
        for field in ("ref", "chars", "bytes", "sha256", "kind", "mime", "encoding", "is_text"):
            result.pop(f"source_{field}", None)
            mapping.pop(f"source_{field}", None)
        if isinstance(data, dict):
            result["data"] = mapping
        return result
    source_ref = str(result.get("source_ref") or mapping.get("source_ref") or "")
    if not source_ref:
        return result
    values = {}
    for field in ("ref", "chars", "bytes", "sha256", "kind", "mime", "encoding", "is_text"):
        key = f"source_{field}"
        if key in result:
            values[key] = result[key]
        elif key in mapping:
            values[key] = mapping[key]
    result.update(values)
    mapping.update(values)
    if isinstance(data, dict):
        result["data"] = mapping
    return result


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _requested_path(payload: dict[str, Any], metadata: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    args = metadata.get("tool_arguments") if isinstance(metadata.get("tool_arguments"), dict) else {}
    return str(
        data.get("requested_path")
        or result.get("requested_path")
        or data.get("path")
        or args.get("path")
        or payload.get("path")
        or ""
    )


def _content_metadata(payload: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_chars": payload.get("content_chars") or data.get("content_chars") or 0,
        "content_bytes": payload.get("content_bytes") or data.get("content_bytes") or 0,
        "content_sha256": payload.get("content_sha256") or data.get("content_sha256") or "",
        "content_externalized": bool(payload.get("content_externalized") or data.get("content_externalized")),
        "compacted": True,
    }


def _source_metadata(payload: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_ref": payload.get("source_ref") or data.get("source_ref") or "",
        "source_chars": payload.get("source_chars") if "source_chars" in payload else data.get("source_chars"),
        "source_bytes": payload.get("source_bytes") if payload.get("source_bytes") is not None else data.get("source_bytes", 0),
        "source_sha256": payload.get("source_sha256") or data.get("source_sha256") or "",
        "source_kind": payload.get("source_kind") or data.get("source_kind") or "",
        "source_mime": payload.get("source_mime") or data.get("source_mime") or "",
        "source_encoding": payload.get("source_encoding") if "source_encoding" in payload else data.get("source_encoding"),
        "source_is_text": bool(payload.get("source_is_text") if "source_is_text" in payload else data.get("source_is_text")),
    }


def _preview(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + f"...[truncated {len(value) - limit} chars]"


def _tail(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[-limit:]


def _compact_current_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": step.get("index"),
        "tool_name": step.get("tool_name"),
        "status": step.get("status"),
        "capability": step.get("capability"),
    }


__all__ = [
    "build_tool_result_card",
    "compact_observation_for_model",
    "compact_tool_result_for_model",
    "estimate_observation_chars",
    "observation_compaction_summary",
]
