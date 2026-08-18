"""Resolve structured Coding Intent into one normalized decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.intent_schema import IntentClassification


READ_ONLY_ACTIONS = frozenset({"inspect", "explain", "review"})
VALIDATION_ACTIONS = frozenset({"test", "validate"})
EDIT_ACTIONS = frozenset({"edit", "fix", "implement", "refactor"})
KNOWN_ACTIONS = READ_ONLY_ACTIONS | VALIDATION_ACTIONS | EDIT_ACTIONS | frozenset({"unknown"})
CODING_TOOL_FAMILIES = frozenset({"coding", "validation", "git"})
CODE_EDIT_TOOLS = frozenset({"replace_in_file", "write_file"})


@dataclass(frozen=True)
class ResolvedCodingIntent:
    is_coding_task: bool
    needs_code_edit: bool
    needs_validation: bool
    needs_git: bool
    action: str
    reason: str


def resolve_coding_intent(intent: IntentClassification) -> ResolvedCodingIntent:
    """Resolve Coding intent using only structured IntentClassification data."""

    action = _action(getattr(intent.coding, "action", None))
    requested_tools = {str(name).strip().lower() for name in intent.tools.requested_tool_names if str(name).strip()}
    requested_family = str(intent.tools.requested_tool_family or "").strip().lower()
    has_edit_tool = bool(requested_tools.intersection(CODE_EDIT_TOOLS))
    coding_signal = bool(
        intent.intent_type == "coding"
        or intent.task_type == "coding"
        or intent.workflow_kind == "coding"
        or intent.flags.needs_code_edit
        or requested_family in CODING_TOOL_FAMILIES
        or action != "unknown"
    )

    explicit_read_only = action in READ_ONLY_ACTIONS and not intent.flags.needs_code_edit and not has_edit_tool
    explicit_validation = action in VALIDATION_ACTIONS and not intent.flags.needs_code_edit and not has_edit_tool
    edit_signal = bool(
        intent.flags.needs_code_edit
        or action in EDIT_ACTIONS
        or has_edit_tool
    )

    if explicit_read_only:
        needs_code_edit = False
        reason = f"coding_action_{action}"
    elif explicit_validation:
        needs_code_edit = False
        reason = f"coding_action_{action}"
    elif edit_signal:
        needs_code_edit = True
        reason = _edit_reason(intent, action, requested_family, has_edit_tool)
    else:
        needs_code_edit = False
        reason = "coding_signal_without_edit"

    needs_validation = bool(intent.flags.needs_validation or action in VALIDATION_ACTIONS or needs_code_edit)
    needs_git = bool(intent.flags.needs_git or needs_code_edit)

    return ResolvedCodingIntent(
        is_coding_task=coding_signal,
        needs_code_edit=needs_code_edit,
        needs_validation=needs_validation,
        needs_git=needs_git,
        action=action,
        reason=reason,
    )


def normalize_coding_action(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in KNOWN_ACTIONS else "unknown"


def _action(value: object) -> str:
    return normalize_coding_action(value)


def _edit_reason(intent: IntentClassification, action: str, requested_family: str, has_edit_tool: bool) -> str:
    if intent.flags.needs_code_edit:
        return "flags.needs_code_edit"
    if action in EDIT_ACTIONS:
        return f"coding_action_{action}"
    if has_edit_tool:
        return "requested_code_edit_tool"
    return "coding_intent_normalized"
