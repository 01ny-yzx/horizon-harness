"""Standard structured observation context for terminal finalization."""

from __future__ import annotations

import json
from typing import Any

from core.observation_compaction import (
    bounded_read_file_page,
    compact_observation_for_model,
    estimate_observation_chars,
)
from core.tool_execution_authorization import base_tool_name
from core.unicode_safety import sanitize_unicode


SCHEMA_VERSION = "final_observation_context_v1"
DEFAULT_PREVIEW_CHARS = 1200


class FinalObservationContext(list[dict[str, Any]]):
    """List-compatible context carrying non-model-visible dedupe metrics."""

    def __init__(
        self,
        items: list[dict[str, Any]],
        *,
        raw_observation_count: int,
        duplicates_removed: int,
        expected_call_ids: list[str],
    ) -> None:
        super().__init__(items)
        self.raw_observation_count = raw_observation_count
        self.duplicates_removed = duplicates_removed
        self.expected_call_ids = list(expected_call_ids)
        self.represented_call_ids = _ordered_unique(
            str(item.get("call_id") or "") for item in items
        )
        represented = set(self.represented_call_ids)
        self.missing_call_ids = [call_id for call_id in self.expected_call_ids if call_id not in represented]
        self.coverage_complete = not self.missing_call_ids


def build_final_observation_context(
    task_state: Any,
    outcome: Any | None,
) -> FinalObservationContext:
    """Build current-task final context only from structured runtime results."""

    task_observations = _task_state_completion_observations(task_state)
    candidates: list[tuple[str, dict[str, Any]]] = []
    candidates.extend(("task_state", item) for item in task_observations)
    terminal_observation = _outcome_observation(outcome)
    if terminal_observation:
        candidates.append(("terminal_outcome", _with_outcome_fields(terminal_observation, outcome)))
    expected_call_ids = _expected_terminal_call_ids(
        task_state,
        task_observations=task_observations,
        terminal_observation=terminal_observation,
    )

    groups: dict[int, dict[str, Any]] = {}
    identity_groups: dict[str, int] = {}
    content_groups: dict[str, int] = {}
    next_group_id = 0
    runtime_lane = _runtime_lane(task_state)
    for index, (source, observation) in enumerate(candidates):
        item = _build_one_context(source, observation, outcome, runtime_lane=runtime_lane)
        identities = _observation_identities(observation)
        matching_groups = {identity_groups[identity] for identity in identities if identity in identity_groups}
        content_fingerprint = _context_fingerprint(item) if not identities else ""
        if content_fingerprint and content_fingerprint in content_groups:
            matching_groups.add(content_groups[content_fingerprint])
        if matching_groups:
            group_id = min(matching_groups, key=lambda candidate: int(groups[candidate]["index"]))
            group = groups[group_id]
            for duplicate_group_id in sorted(matching_groups - {group_id}):
                duplicate = groups.pop(duplicate_group_id)
                group["index"] = min(int(group["index"]), int(duplicate["index"]))
                group["identities"].update(duplicate["identities"])
                group["item"] = _merge_context_items(group["item"], duplicate["item"])
                for identity in duplicate["identities"]:
                    identity_groups[identity] = group_id
            group["item"] = _merge_context_items(group["item"], item)
            group["identities"].update(identities)
        else:
            group_id = next_group_id
            next_group_id += 1
            group = {"index": index, "item": item, "identities": set(identities)}
            groups[group_id] = group
        for identity in group["identities"]:
            identity_groups[identity] = group_id
        if content_fingerprint:
            content_groups[content_fingerprint] = group_id

    context: list[dict[str, Any]] = []
    for group in sorted(groups.values(), key=lambda value: int(value["index"])):
        item = _with_group_identities(dict(group["item"]), set(group["identities"]))
        context.append(item)
    sanitized = [sanitize_unicode(item) for item in context]
    return FinalObservationContext(
        sanitized,
        raw_observation_count=len(candidates),
        duplicates_removed=max(0, len(candidates) - len(groups)),
        expected_call_ids=expected_call_ids,
    )


def final_observation_context_trace_summary(context: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a compact trace summary for final observation context."""

    items = list(context or [])
    total_chars = estimate_observation_chars(items)
    tools = [str(item.get("base_tool") or item.get("tool") or "") for item in items if item.get("base_tool") or item.get("tool")]
    sources = [str(item.get("source") or "") for item in items if item.get("source")]
    source_counts: dict[str, int] = {}
    for source in sources:
        source_counts[source] = source_counts.get(source, 0) + 1
    call_ids = list(dict.fromkeys(str(item.get("call_id") or "") for item in items if item.get("call_id")))
    expected_call_ids = list(getattr(context, "expected_call_ids", call_ids) or [])
    represented_call_ids = list(getattr(context, "represented_call_ids", call_ids) or [])
    missing_call_ids = list(getattr(context, "missing_call_ids", []) or [])
    schema_versions = sorted({str(item.get("schema_version") or "") for item in items if item.get("schema_version")})
    return sanitize_unicode(
        {
            "raw_observation_count": int(getattr(context, "raw_observation_count", len(items)) or 0),
            "observation_count": len(items),
            "duplicates_removed": int(getattr(context, "duplicates_removed", 0) or 0),
            "tools": tools,
            "total_chars": total_chars,
            "truncated_count": sum(1 for item in items if bool(item.get("truncated"))),
            "sources": sources,
            "source_counts": source_counts,
            "call_ids": call_ids,
            "expected_call_ids": expected_call_ids,
            "represented_call_ids": represented_call_ids,
            "missing_call_ids": missing_call_ids,
            "expected_call_count": len(expected_call_ids),
            "represented_call_count": len(represented_call_ids),
            "coverage_complete": bool(getattr(context, "coverage_complete", not missing_call_ids)),
            "schema_version": schema_versions[0] if len(schema_versions) == 1 else schema_versions,
        }
    )


def _build_one_context(source: str, observation: dict[str, Any], outcome: Any, *, runtime_lane: str) -> dict[str, Any]:
    tool = str(observation.get("tool") or observation.get("tool_name") or getattr(outcome, "tool", "") or "").strip()
    base = base_tool_name(tool)
    compacted = compact_observation_for_model(observation, runtime_lane=runtime_lane, max_preview_chars=DEFAULT_PREVIEW_CHARS)
    summary = dict(compacted.get("model_visible_summary") or {})
    trace_summary = dict(compacted.get("trace_summary") or {})
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    outcome_policy_code = getattr(outcome, "policy_code", "") if source == "terminal_outcome" else ""
    policy_code = _first_text(observation.get("policy_code"), data.get("policy_code"), outcome_policy_code)
    error_code = _first_text(observation.get("error_code"), data.get("error_code"), data.get("code"), summary.get("error_code"))
    preview = _first_text(
        summary.get("preview"),
        summary.get("excerpt"),
        summary.get("content_preview"),
        summary.get("text_preview"),
        summary.get("stdout_preview"),
        summary.get("stderr_preview"),
    )
    refs = _refs(compacted, summary)
    data_summary = _tool_data_summary(base, summary, data)
    read_page = bounded_read_file_page(data) if base == "read_file" else None
    stopped_before_execution = (
        data.get("stopped_before_execution")
        if "stopped_before_execution" in data
        else summary.get("stopped_before_execution")
    )
    if stopped_before_execution is not None:
        data_summary["stopped_before_execution"] = bool(stopped_before_execution)
    item = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "call_id": _first_text(
            observation.get("call_id"),
            observation.get("tool_call_id"),
            observation.get("provider_call_id"),
        ),
        "provider_call_id": _first_text(observation.get("provider_call_id")),
        "observation_id": _first_text(observation.get("observation_id")),
        "tool": tool or str(summary.get("tool_name") or ""),
        "base_tool": base_tool_name(tool or str(summary.get("tool_name") or "")),
        "kind": str(observation.get("kind") or summary.get("kind") or ""),
        "status": str(observation.get("status") or summary.get("status") or ""),
        "success": observation.get("success") is True or summary.get("success") is True,
        "summary": _summary_text(base, summary, observation),
        "error": _first_text(observation.get("error"), data.get("error"), data.get("message"), summary.get("error")),
        "error_code": error_code,
        "policy_code": policy_code,
        "arguments_summary": _arguments_summary(observation),
        "data_summary": data_summary,
        "preview": preview,
        "refs": refs,
        "truncated": (
            bool(read_page["truncated"])
            if read_page is not None
            else bool(summary.get("truncated") or trace_summary.get("has_large_content") or refs)
        ),
        "compacted": True,
        "original_chars": int(compacted.get("original_chars") or trace_summary.get("original_chars") or 0),
        "compacted_chars": int(compacted.get("compacted_chars") or trace_summary.get("compacted_chars") or 0),
    }
    if not item["policy_code"] and (
        source == "terminal_outcome" or str(item.get("status") or "").lower() == "blocked"
    ):
        item["policy_code"] = item["error_code"] if str(getattr(outcome, "kind", "") or "") == "terminal_policy_blocked" else ""
    return {key: value for key, value in item.items() if value not in ("", None, {}, [])}


def _tool_data_summary(base: str, summary: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if base in {"fetch_url", "web_read"}:
        return _pick(
            data,
            summary,
            ("url", "content_type", "format", "error", "error_code", "http_status"),
        )
    if base == "web_search":
        result = _pick(data, summary, ("query", "result_count", "provider"))
        result["results"] = _bounded_results(data.get("results") or summary.get("results") or [])
        return {key: value for key, value in result.items() if value not in ("", None, [], {})}
    if base == "read_file":
        result = _pick(data, summary, ("requested_path", "path", "file_path", "filename", "line_start", "line_end", "next_offset", "total_lines", "page_bytes", "preview", "excerpt", "error", "error_code", "path_grounding", "source_ref", "source_kind", "source_chars", "source_bytes", "source_sha256", "source_mime", "source_encoding", "source_is_text", "content_ref", "chars", "visible_chars", "preview_chars", "output_text_chars", "truncated"))
        content = data.get("content") if isinstance(data.get("content"), str) else summary.get("content")
        if isinstance(content, str) and content:
            result["content"] = content
        return result
    if base == "read_document":
        result = _pick(
            data,
            summary,
            (
                "requested_path", "path", "file_path", "filename", "preview", "excerpt", "error", "error_code", "path_grounding", "pages", "page_count",
                "table_count", "total_sheet_count", "parsed_sheet_count", "visible_table_count",
                "reader_omitted_tables", "projection_omitted_tables", "omitted_tables", "omitted_fields",
                "reader_truncated", "truncated", "compacted", "source_ref", "source_kind", "source_chars",
                "source_bytes", "source_sha256", "source_mime", "source_encoding", "source_is_text",
                "content_ref", "content_chars", "content_bytes", "content_sha256", "content_externalized",
                "chars", "visible_chars", "preview_chars", "output_text_chars",
            ),
        )
        tables = data.get("tables") if "tables" in data else summary.get("tables")
        bounded_tables = _bounded_document_tables(tables)
        if bounded_tables:
            result["tables"] = bounded_tables
        return result
    if base in {
        "load_document",
        "load_documents_from_directory",
        "rebuild_chunks_for_document",
    }:
        return _pick(
            data,
            summary,
            (
                "requested_path",
                "path",
                "file_path",
                "document_id",
                "document_ids",
                "store_status",
                "document_stored",
                "documents_stored",
                "chunks_stored",
                "chunk_count",
                "chunks_count",
                "loaded_count",
                "failed_count",
                "create_chunks",
                "content_ref",
                "error",
                "error_code",
            ),
        )
    if base in {"write_file", "replace_in_file"}:
        return _pick(data, summary, ("path", "output_path", "source_ref", "source_kind", "source_chars", "source_bytes", "source_sha256", "source_mime", "source_encoding", "source_is_text", "chars_written", "bytes", "bytes_written", "collision_resolved", "success", "error", "error_code"))
    if base == "sandbox_exec":
        return _pick(
            data,
            summary,
            ("command", "cwd", "exit_code", "returncode", "stdout_preview", "stderr_preview", "stdout_tail", "stderr_tail", "timed_out", "error_code"),
        )
    if base.startswith("browser_") or base in {"browser_extract_text", "browser_list_links", "browser_screenshot"}:
        return _pick(data, summary, ("url", "title", "text_preview", "content_preview", "preview", "screenshot_ref", "error", "error_code"))
    if _looks_mcp(data, summary):
        return _pick(data, summary, ("server", "tool", "mcp_tool_name", "success", "result_preview", "preview", "error", "error_code"))
    return _generic_data_summary(summary, data)


def _generic_data_summary(summary: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "path",
        "file_path",
        "url",
        "command",
        "exit_code",
        "returncode",
        "error",
        "error_code",
        "policy_code",
        "code",
        "block_reason",
        "preview",
        "content_preview",
        "text_preview",
        "stdout_preview",
        "stderr_preview",
        "item_count",
        "items",
    )
    return _pick(data, summary, keys)


def _outcome_observation(outcome: Any) -> dict[str, Any]:
    metadata = getattr(outcome, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    observation = metadata.get("observation")
    return dict(observation) if isinstance(observation, dict) else {}


def _with_outcome_fields(observation: dict[str, Any], outcome: Any) -> dict[str, Any]:
    result = dict(observation)
    result.setdefault("tool", str(getattr(outcome, "tool", "") or ""))
    user_message = str(getattr(outcome, "user_message", "") or "").strip()
    if user_message:
        result.setdefault("summary", user_message)
    if str(getattr(outcome, "policy_code", "") or "").strip():
        result.setdefault("policy_code", str(getattr(outcome, "policy_code") or "").strip())
    return result


def _task_state_completion_observations(task_state: Any) -> list[dict[str, Any]]:
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    result: list[dict[str, Any]] = []
    for item in metadata.get("completion_observations") or []:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def _expected_terminal_call_ids(
    task_state: Any,
    *,
    task_observations: list[dict[str, Any]],
    terminal_observation: dict[str, Any],
) -> list[str]:
    expected: list[str] = []
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    for call_id in metadata.get("structured_tool_call_ids") or []:
        _append_unique(expected, str(call_id or ""))
    contract = metadata.get("build_step_contract")
    contract = contract if isinstance(contract, dict) else {}
    steps = [step for step in contract.get("steps") or [] if isinstance(step, dict)]
    for step in sorted(steps, key=lambda value: int(value.get("index") or 0)):
        if str(step.get("status") or "").strip().lower() not in {"completed", "failed", "blocked"}:
            continue
        _append_unique(expected, str(step.get("call_id") or ""))
    for observation in task_observations:
        if _is_terminal_observation(observation):
            _append_unique(expected, _observation_call_id(observation))
    if terminal_observation and _is_terminal_observation(terminal_observation):
        _append_unique(expected, _observation_call_id(terminal_observation))
    return expected


def _is_terminal_observation(observation: dict[str, Any]) -> bool:
    status = str(observation.get("status") or "").strip().lower()
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    data_status = str(data.get("status") or "").strip().lower()
    terminal = {"success", "completed", "failed", "error", "blocked"}
    return bool(observation.get("success") is True or status in terminal or data_status in terminal)


def _observation_call_id(observation: dict[str, Any]) -> str:
    return _first_text(
        observation.get("call_id"),
        observation.get("tool_call_id"),
        observation.get("provider_call_id"),
    )


def _append_unique(values: list[str], value: str) -> None:
    normalized = str(value or "").strip()
    if normalized and normalized not in values:
        values.append(normalized)


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        _append_unique(result, str(value or ""))
    return result


def _arguments_summary(observation: dict[str, Any]) -> dict[str, Any]:
    metadata = observation.get("metadata") if isinstance(observation.get("metadata"), dict) else {}
    args = metadata.get("tool_arguments") if isinstance(metadata.get("tool_arguments"), dict) else {}
    if not args:
        return {}
    result: dict[str, Any] = {}
    for key, value in list(args.items())[:12]:
        result[str(key)] = _bounded_value(value)
    return result


def _pick(data: dict[str, Any], summary: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        value = data.get(key) if key in data else summary.get(key)
        if value not in (None, "", [], {}):
            result[key] = _bounded_value(value)
    return result


def _bounded_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    bounded = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        bounded.append(_pick(item, item, ("url", "title", "snippet", "score")))
    return bounded


def _bounded_document_tables(tables: Any) -> list[dict[str, Any]]:
    """Defensively reduce an already-canonical document table summary."""

    if not isinstance(tables, list):
        return []
    keys = (
        "name", "source_row_count", "source_column_count", "parsed_row_count", "parsed_column_count",
        "visible_row_count", "visible_column_count", "row_count", "column_count", "headers",
        "preview_rows", "reader_omitted_rows", "reader_omitted_columns", "projection_omitted_rows",
        "projection_omitted_columns", "omitted_rows", "omitted_columns", "reader_truncated", "truncated",
    )
    result: list[dict[str, Any]] = []
    for table in tables[:5]:
        if not isinstance(table, dict):
            continue
        item = {key: table[key] for key in keys if key in table and key not in {"headers", "preview_rows"}}
        if isinstance(item.get("name"), str):
            item["name"] = item["name"][:120]
        headers = table.get("headers")
        if isinstance(headers, list):
            item["headers"] = [_bounded_table_cell(value) for value in headers[:12]]
        preview_rows = next(
            (table.get(key) for key in ("preview_rows", "rows", "data", "values") if isinstance(table.get(key), (list, tuple))),
            [],
        )
        if isinstance(preview_rows, (list, tuple)):
            item["preview_rows"] = [
                [_bounded_table_cell(value) for value in (list(row) if isinstance(row, (list, tuple)) else [row])[:12]]
                for row in preview_rows[:8]
            ]
        visible_rows = item.get("preview_rows") if isinstance(item.get("preview_rows"), list) else []
        visible_headers = item.get("headers") if isinstance(item.get("headers"), list) else []
        visible_row_count = len(visible_rows)
        visible_column_count = max([len(visible_headers), *(len(row) for row in visible_rows)] or [0])
        parsed_rows = int(item.get("parsed_row_count") or visible_row_count)
        parsed_columns = int(item.get("parsed_column_count") or visible_column_count)
        source_rows = int(item.get("source_row_count") or item.get("row_count") or parsed_rows)
        source_columns = int(item.get("source_column_count") or item.get("column_count") or parsed_columns)
        item["source_row_count"] = source_rows
        item["source_column_count"] = source_columns
        item["parsed_row_count"] = parsed_rows
        item["parsed_column_count"] = parsed_columns
        item["row_count"] = source_rows
        item["column_count"] = source_columns
        item["visible_row_count"] = visible_row_count
        item["visible_column_count"] = visible_column_count
        item["reader_omitted_rows"] = max(0, source_rows - parsed_rows)
        item["reader_omitted_columns"] = max(0, source_columns - parsed_columns)
        item["projection_omitted_rows"] = max(0, parsed_rows - visible_row_count)
        item["projection_omitted_columns"] = max(0, parsed_columns - visible_column_count)
        item["omitted_rows"] = max(0, source_rows - visible_row_count)
        item["omitted_columns"] = max(0, source_columns - visible_column_count)
        item["reader_truncated"] = bool(item.get("reader_truncated") or item["reader_omitted_rows"] or item["reader_omitted_columns"])
        item["truncated"] = bool(item.get("truncated") or item["omitted_rows"] or item["omitted_columns"])
        result.append(item)
    return result


def _bounded_table_cell(value: Any) -> Any:
    if isinstance(value, str):
        return value[:80]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:80]


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return _preview(value, DEFAULT_PREVIEW_CHARS)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, list):
        return [_bounded_value(item) for item in value[:8]]
    if isinstance(value, dict):
        return {str(key): _bounded_value(item) for key, item in list(value.items())[:12]}
    return str(value)[:DEFAULT_PREVIEW_CHARS]


def _summary_text(base: str, summary: dict[str, Any], observation: dict[str, Any]) -> str:
    for key in ("summary", "reason", "error", "status"):
        text = str(summary.get(key) or observation.get(key) or "").strip()
        if text:
            return _preview(text, 500)
    return f"{base or 'tool'} observation"


def _refs(compacted: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    content_ref = str(compacted.get("content_ref") or summary.get("content_ref") or "").strip()
    if content_ref:
        refs.append({"type": "content_ref", "ref": content_ref})
    source_ref = str(compacted.get("source_ref") or summary.get("source_ref") or "").strip()
    if source_ref:
        refs.append({"type": "source_ref", "ref": source_ref})
    screenshot_ref = str(summary.get("screenshot_ref") or "").strip()
    if screenshot_ref:
        refs.append({"type": "screenshot_ref", "ref": screenshot_ref})
    return refs


def _looks_mcp(data: dict[str, Any], summary: dict[str, Any]) -> bool:
    return any(key in data or key in summary for key in ("server", "mcp_tool_name", "mcp_tool", "mcp"))


def _runtime_lane(task_state: Any) -> str:
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    return str(metadata.get("runtime_lane") or "")


def _context_fingerprint(item: dict[str, Any]) -> str:
    canonical = {
        "tool": item.get("base_tool") or item.get("tool") or "",
        "arguments": item.get("arguments_summary") or {},
        "success": item.get("success"),
        "status": item.get("status") or "",
        "kind": item.get("kind") or "",
        "error_code": item.get("error_code") or "",
        "error": item.get("error") or "",
        "data": item.get("data_summary") or {},
    }
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _observation_identities(observation: dict[str, Any]) -> list[str]:
    call_identities = _identities_for_keys(
        observation,
        ("call_id", "tool_call_id"),
        prefix="call",
    )
    if call_identities:
        return call_identities
    provider_identities = _identities_for_keys(
        observation,
        ("provider_call_id",),
        prefix="provider",
    )
    if provider_identities:
        return provider_identities
    return _identities_for_keys(
        observation,
        ("observation_id",),
        prefix="observation",
    )


def _identities_for_keys(
    observation: dict[str, Any],
    keys: tuple[str, ...],
    *,
    prefix: str,
) -> list[str]:
    result: list[str] = []
    for key in keys:
        value = str(observation.get(key) or "").strip()
        identity = f"{prefix}:{value}" if value else ""
        if identity and identity not in result:
            result.append(identity)
    return result


def _with_group_identities(item: dict[str, Any], identities: set[str]) -> dict[str, Any]:
    for prefix, key in (("call:", "call_id"), ("provider:", "provider_call_id"), ("observation:", "observation_id")):
        if item.get(key):
            continue
        value = next((identity[len(prefix) :] for identity in sorted(identities) if identity.startswith(prefix)), "")
        if value:
            item[key] = value
    return item


def _merge_context_items(primary: dict[str, Any], supplement: dict[str, Any]) -> dict[str, Any]:
    """Fill and enrich one canonical context item without changing its source/order."""

    result = dict(primary)
    for key, value in supplement.items():
        if key == "source" or value in (None, "", [], {}):
            continue
        current = result.get(key)
        if current in (None, "", [], {}):
            result[key] = value
        elif isinstance(current, dict) and isinstance(value, dict):
            result[key] = _merge_context_items(current, value)
        elif isinstance(current, str) and isinstance(value, str) and len(value) > len(current):
            result[key] = value
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = current + [item for item in value if item not in current]
    return result


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _preview(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + f"...[truncated {len(value) - limit} chars]"


__all__ = [
    "FinalObservationContext",
    "SCHEMA_VERSION",
    "build_final_observation_context",
    "final_observation_context_trace_summary",
]
