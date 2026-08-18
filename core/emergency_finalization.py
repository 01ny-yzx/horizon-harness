"""Minimal deterministic finalization when the terminal responder is unusable."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from core.finalization_context_snapshot import FinalizationContextSnapshot
from core.unicode_safety import sanitize_unicode


_INTERNAL_ID_FIELDS = frozenset(
    {
        "task_id",
        "call_id",
        "tool_call_id",
        "provider_call_id",
        "observation_id",
        "step_index",
    }
)
_INTERNAL_ID_TEXT_RE = re.compile(
    r"(?i)\b(?:task_id|call_id|tool_call_id|provider_call_id|observation_id|step_index)"
    r"\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class EmergencyFinalizationResult:
    content: str
    used: bool
    reason: str
    fallback_reason: str
    tool: str = ""
    outcome_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def build_emergency_finalization(
    snapshot: FinalizationContextSnapshot,
    *,
    reason: str,
    fallback_reason: str,
) -> EmergencyFinalizationResult:
    """Build a bounded answer only from one isolated finalization snapshot."""

    if not isinstance(snapshot, FinalizationContextSnapshot):
        raise ValueError("finalization_context_snapshot_required")
    model_context = snapshot.model_context
    context = [
        dict(item)
        for item in model_context.get("results") or []
        if isinstance(item, dict)
    ]
    final_status = str(model_context.get("final_status") or "failed")
    successes = [item for item in context if item.get("success") is True]
    blocked = [
        item
        for item in context
        if str(item.get("status") or "").lower() == "blocked"
        or bool(item.get("policy_code"))
    ]
    failures = [
        item
        for item in context
        if item not in blocked
        and item.get("success") is not True
        and str(item.get("status") or "").lower() in {"failed", "error"}
    ]

    unsuccessful = [
        item
        for item in context
        if item.get("success") is not True
        and item not in blocked
        and str(item.get("status") or "").lower()
        in {"failed", "error", "partial", "stopped"}
    ]

    if successes and (blocked or unsuccessful):
        lines = ["任务部分完成。"]
        lines.extend(f"- 已完成：{_success_summary(item)}" for item in successes)
        lines.extend(f"- 未完成：{_blocked_summary(item)}" for item in blocked)
        lines.extend(f"- 未完成：{_failure_summary(item)}" for item in unsuccessful)
    elif final_status == "completed":
        lines = [_success_summary(item) for item in successes] or [
            "操作已完成，但没有可进一步展示的结果。"
        ]
    elif final_status == "blocked" or blocked:
        lines = [_blocked_summary(item) for item in blocked] or ["操作未执行。"]
    elif final_status == "stopped":
        lines = [_failure_summary(item) for item in failures] or [
            "检测到重复工具调用，任务已在再次执行前停止。"
        ]
    else:
        lines = [_failure_summary(item) for item in failures]
        if not lines:
            lines = ["任务未能完成，且没有可确认的工具结果。"]

    coverage = model_context.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    coverage_complete = bool(coverage.get("complete", snapshot.coverage_complete))
    if not coverage_complete:
        lines.append("当前可确认结果不完整，只汇总已有结构化结果。")
    content = "\n".join(line for line in lines if line).strip()
    metadata = sanitize_unicode(
        {
            "observation_count": len(context),
            "success_count": len(successes),
            "blocked_count": len(blocked),
            "failure_count": len(failures),
            "coverage_complete": coverage_complete,
            "missing_observation_count": int(
                coverage.get("missing_result_count") or 0
            ),
            "snapshot_hash": snapshot.snapshot_hash,
        }
    )
    return EmergencyFinalizationResult(
        content=content or "任务未能完成，且没有可确认的工具结果。",
        used=True,
        reason=str(reason or ""),
        fallback_reason=str(fallback_reason or ""),
        tool=str((context[-1] if context else {}).get("tool") or ""),
        outcome_kind=final_status,
        metadata=metadata,
    )


def build_emergency_finalization_trace_summary(
    result: EmergencyFinalizationResult,
) -> dict[str, Any]:
    """Return a compact trace summary without observation identities."""

    return sanitize_unicode(
        {
            "used": result.used,
            "reason": result.reason,
            "fallback_reason": result.fallback_reason,
            "outcome_kind": result.outcome_kind,
            "tool": result.tool,
            **dict(result.metadata or {}),
        }
    )


def _success_summary(item: dict[str, Any]) -> str:
    tool = str(item.get("tool") or "工具")
    detail = _visible_detail(item)
    return _redact_internal_ids(f"{tool} 已成功完成{f'：{detail}' if detail else '。'}")


def _blocked_summary(item: dict[str, Any]) -> str:
    tool = str(item.get("tool") or "操作")
    policy = str(item.get("policy_code") or item.get("error_code") or "")
    error = str(item.get("error") or "")
    detail = "；".join(value for value in (policy, error) if value)
    return _redact_internal_ids(
        f"{tool} 未执行{f'：{detail}' if detail else '，因为运行时策略阻止了该操作。'}"
    )


def _failure_summary(item: dict[str, Any]) -> str:
    tool = str(item.get("tool") or "工具")
    values: list[str] = []
    for value in (
        item.get("error"),
        item.get("error_code"),
        _data_value(item, "exit_code"),
        _data_value(item, "returncode"),
        _data_value(item, "stderr"),
        _data_value(item, "stderr_preview"),
    ):
        text = _bounded_text(value)
        if text and text not in values:
            values.append(text)
    detail = "；".join(values)
    return _redact_internal_ids(
        f"{tool} 执行失败{f'：{detail}' if detail else '。'}"
    )


def _visible_detail(item: dict[str, Any]) -> str:
    values: list[str] = []
    summary = _bounded_text(item.get("summary"))
    if summary.lower() not in {"success", "completed", "complete"}:
        if summary:
            values.append(summary)
    preview = _bounded_text(item.get("preview"))
    if preview:
        values.append(preview)
    data = item.get("data_summary")
    data = data if isinstance(data, dict) else {}
    for key in (
        "path",
        "output_path",
        "file_path",
        "url",
        "title",
        "active_provider",
        "provider",
        "document_id",
        "store_status",
        "document_stored",
        "chunks_stored",
        "chunk_count",
        "chunks_count",
        "exit_code",
        "stdout",
        "stdout_preview",
        "content_preview",
        "text_preview",
        "preview",
    ):
        if key in _INTERNAL_ID_FIELDS:
            continue
        text = _bounded_text(data.get(key))
        if text and text not in values:
            values.append(text)
    return "；".join(values[:8])


def _data_value(item: dict[str, Any], key: str) -> Any:
    data = item.get("data_summary")
    return data.get(key) if isinstance(data, dict) else None


def _bounded_text(value: Any, limit: int = 600) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = _strip_internal_fields(value)
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = _redact_internal_ids(text.strip())
    return text if len(text) <= limit else f"{text[:limit]}…"


def _strip_internal_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_internal_fields(item)
            for key, item in value.items()
            if str(key) not in _INTERNAL_ID_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_internal_fields(item) for item in value]
    return value


def _redact_internal_ids(text: str) -> str:
    return _INTERNAL_ID_TEXT_RE.sub("", str(text or "")).strip(" ,;")


__all__ = [
    "EmergencyFinalizationResult",
    "build_emergency_finalization",
    "build_emergency_finalization_trace_summary",
]
