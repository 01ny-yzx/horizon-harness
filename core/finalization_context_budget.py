"""Token-aware compaction for isolated finalization snapshots."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any

from core.context_budget import estimate_text_tokens
from core.finalization_context_snapshot import (
    FinalizationContextSnapshot,
    rebuild_finalization_context_snapshot,
)
from core.unicode_safety import sanitize_unicode
from providers.base import LLMCapabilities
from providers.capabilities import resolve_effective_model_limits


@dataclass(frozen=True)
class FinalizationContextBudgetDecision:
    action: str
    reason: str
    original_tokens: int
    final_tokens: int
    usable_input_tokens: int
    budget_satisfied: bool
    result_count: int
    truncated_result_count: int
    dropped_result_count: int
    original_chars: int = 0
    final_chars: int = 0
    saved_chars: int = 0
    reserved_output_tokens: int = 0
    raw_model_context_tokens: int = 0
    raw_model_input_tokens: int | None = None
    raw_model_output_tokens: int | None = None
    effective_model_input_tokens: int = 0
    requested_output_tokens: int = 0
    model_limit_correction_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


def apply_finalization_context_budget(
    snapshot: FinalizationContextSnapshot,
    *,
    model_context_tokens: int | None,
    model_input_tokens: int | None,
    model_output_tokens: int | None,
    reserved_output_tokens: int,
) -> tuple[FinalizationContextSnapshot, FinalizationContextBudgetDecision]:
    """Compact result details without removing any structured result."""

    effective_limits = resolve_effective_model_limits(
        LLMCapabilities(
            max_context_tokens=_positive_int(model_context_tokens),
            max_input_tokens=_positive_int(model_input_tokens),
            max_output_tokens=_positive_int(model_output_tokens),
        ),
        _positive_int(reserved_output_tokens) or 1024,
    )
    usable_tokens = int(effective_limits.usable_input_tokens or 0)
    original_context = snapshot.model_context
    original_tokens = _context_tokens(original_context)
    if original_tokens <= usable_tokens:
        return snapshot, _decision(
            action="keep",
            reason="within_finalization_context_budget",
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            usable_tokens=usable_tokens,
            snapshot=snapshot,
            original_chars=snapshot.model_context_chars,
            truncated_count=sum(
                1
                for item in original_context.get("results") or []
                if isinstance(item, dict) and bool(item.get("truncated"))
            ),
            effective_limits=effective_limits,
        )

    best_context = copy.deepcopy(original_context)
    best_tokens = original_tokens
    best_snapshot = snapshot
    for text_limit, list_limit in (
        (600, 8),
        (320, 5),
        (160, 3),
        (80, 2),
        (32, 1),
        (0, 0),
    ):
        candidate_context = _compact_model_context(
            original_context,
            text_limit=text_limit,
            list_limit=list_limit,
        )
        candidate_tokens = _context_tokens(candidate_context)
        if candidate_tokens <= best_tokens:
            best_context = candidate_context
            best_tokens = candidate_tokens
            best_snapshot = rebuild_finalization_context_snapshot(
                snapshot,
                model_context=candidate_context,
            )
        if candidate_tokens <= usable_tokens:
            break

    satisfied = best_tokens <= usable_tokens
    return best_snapshot, _decision(
        action="compact",
        reason=(
            "finalization_context_compacted"
            if satisfied
            else "finalization_context_budget_unsatisfied"
        ),
        original_tokens=original_tokens,
        final_tokens=best_tokens,
        usable_tokens=usable_tokens,
        snapshot=best_snapshot,
        original_chars=snapshot.model_context_chars,
        truncated_count=sum(
            1
            for item in best_context.get("results") or []
            if isinstance(item, dict) and bool(item.get("truncated"))
        ),
        effective_limits=effective_limits,
        budget_satisfied=satisfied,
    )


def _compact_model_context(
    model_context: dict[str, Any],
    *,
    text_limit: int,
    list_limit: int,
) -> dict[str, Any]:
    compacted = copy.deepcopy(model_context)
    results: list[dict[str, Any]] = []
    for raw in compacted.get("results") or []:
        item = dict(raw) if isinstance(raw, dict) else {}
        changed = False
        result: dict[str, Any] = {
            "tool": str(item.get("tool") or ""),
            "status": str(item.get("status") or ""),
            "success": item.get("success") is True,
            "error_code": str(item.get("error_code") or ""),
            "policy_code": str(item.get("policy_code") or ""),
            "truncated": bool(item.get("truncated")),
        }
        for key in ("summary", "error", "preview"):
            value = str(item.get(key) or "")
            bounded = _bounded_text(value, text_limit)
            if bounded != value:
                changed = True
            if bounded:
                result[key] = bounded
        data_summary, data_changed = _compact_value(
            item.get("data_summary"),
            text_limit=text_limit,
            list_limit=list_limit,
        )
        changed = changed or data_changed
        if data_summary not in (None, "", {}, []):
            result["data_summary"] = data_summary
        result["truncated"] = bool(result["truncated"] or changed)
        results.append(sanitize_unicode(result))
    compacted["results"] = results
    compacted["result_count"] = len(results)
    coverage = compacted.get("coverage")
    if isinstance(coverage, dict):
        coverage["result_count"] = len(results)
    return sanitize_unicode(compacted)


def _compact_value(
    value: Any,
    *,
    text_limit: int,
    list_limit: int,
) -> tuple[Any, bool]:
    if isinstance(value, str):
        bounded = _bounded_text(value, text_limit)
        return bounded, bounded != value
    if isinstance(value, list):
        selected = value[:list_limit] if list_limit > 0 else []
        changed = len(selected) != len(value)
        output: list[Any] = []
        for item in selected:
            compacted, item_changed = _compact_value(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
            )
            output.append(compacted)
            changed = changed or item_changed
        return output, changed
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            compacted, item_changed = _compact_value(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
            )
            output[str(key)] = compacted
            changed = changed or item_changed
        return output, changed
    return copy.deepcopy(value), False


def _bounded_text(value: str, limit: int) -> str:
    text = str(value or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def _context_tokens(model_context: dict[str, Any]) -> int:
    encoded = json.dumps(
        sanitize_unicode(model_context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return max(1, int(estimate_text_tokens(encoded) * 1.08) + 16)


def _decision(
    *,
    action: str,
    reason: str,
    original_tokens: int,
    final_tokens: int,
    usable_tokens: int,
    snapshot: FinalizationContextSnapshot,
    original_chars: int,
    truncated_count: int,
    effective_limits: Any,
    budget_satisfied: bool = True,
) -> FinalizationContextBudgetDecision:
    final_chars = snapshot.model_context_chars
    return FinalizationContextBudgetDecision(
        action=action,
        reason=reason,
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        usable_input_tokens=usable_tokens,
        budget_satisfied=budget_satisfied,
        result_count=snapshot.result_count,
        truncated_result_count=truncated_count,
        dropped_result_count=0,
        original_chars=original_chars,
        final_chars=final_chars,
        saved_chars=max(0, int(original_chars or 0) - final_chars),
        reserved_output_tokens=int(effective_limits.reserved_output_tokens or 0),
        raw_model_context_tokens=int(effective_limits.raw_context_tokens or 0),
        raw_model_input_tokens=effective_limits.raw_input_tokens,
        raw_model_output_tokens=effective_limits.raw_output_tokens,
        effective_model_input_tokens=int(effective_limits.effective_input_tokens or 0),
        requested_output_tokens=int(effective_limits.requested_output_tokens or 0),
        model_limit_correction_reason=str(effective_limits.correction_reason or ""),
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "FinalizationContextBudgetDecision",
    "apply_finalization_context_budget",
]
