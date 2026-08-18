"""Generic helpers for describing the current model-visible tool schema set."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.unicode_safety import sanitize_unicode


@dataclass(frozen=True)
class ToolScopeBudgetDecision:
    """Trace the already-resolved current tool surface without filtering it."""

    runtime_lane: str
    before_count: int
    after_count: int
    removed_tool_names: tuple[str, ...]
    kept_tool_names: tuple[str, ...]
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                "runtime_lane": self.runtime_lane,
                "before_count": self.before_count,
                "after_count": self.after_count,
                "removed_tool_names": list(self.removed_tool_names),
                "kept_tool_names": list(self.kept_tool_names),
                "reason": self.reason,
                "metadata": self.metadata,
            }
        )


def schema_tool_name(schema: dict[str, object]) -> str:
    """Return the function name from one OpenAI-style tool schema."""

    function = schema.get("function")
    if not isinstance(function, dict):
        return ""
    name = function.get("name")
    return name.strip() if isinstance(name, str) else ""


__all__ = ["ToolScopeBudgetDecision", "schema_tool_name"]
