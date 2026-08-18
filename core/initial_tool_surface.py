"""Stable registry-backed tool surface for the initial agent turn."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from core.agent_access_policy import (
    evaluate_agent_tool_access,
    get_agent_access_mode,
)
from core.tool_schema_scope import schema_tool_name
from core.tool_spec import ToolProvider
from core.unicode_safety import sanitize_unicode


@dataclass(frozen=True)
class InitialToolSurfaceDecision:
    enabled: bool
    tool_names: tuple[str, ...]
    schemas: tuple[dict[str, Any], ...]
    schema_chars: int
    excluded_tools: tuple[str, ...]
    exclusion_reasons: dict[str, str] = field(default_factory=dict)
    access_mode: str = "read_only"
    source: str = "registry_availability_permission"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                **asdict(self),
                "tool_names": list(self.tool_names),
                "schemas": list(self.schemas),
                "excluded_tools": list(self.excluded_tools),
            }
        )


def build_initial_tool_surface(
    tool_specs: Mapping[str, Any] | Sequence[Any],
    tool_schemas: Sequence[dict[str, Any]],
    *,
    access_mode: str,
) -> InitialToolSurfaceDecision:
    """Build the complete currently available, permission-allowed tool surface."""

    mode = get_agent_access_mode(access_mode)
    specs = _spec_mapping(tool_specs)
    permission_schemas = resolve_permission_tool_schemas(
        specs,
        tool_schemas,
        access_mode=mode,
    )
    kept_names = list(permission_tool_names(permission_schemas))
    kept_schemas = list(permission_schemas)
    kept_name_set = set(kept_names)
    excluded: list[str] = []
    reasons: dict[str, str] = {}
    seen: set[str] = set()
    for schema in tool_schemas:
        name = schema_tool_name(schema)
        if not name or name in seen:
            continue
        seen.add(name)
        if name in kept_name_set:
            continue
        spec = specs.get(name)
        reason = _exclusion_reason(name, spec, schema, mode)
        excluded.append(name)
        reasons[name] = reason or "permission_denied"

    schema_chars = _json_chars(kept_schemas) if kept_schemas else 0

    return InitialToolSurfaceDecision(
        enabled=bool(kept_schemas),
        tool_names=tuple(kept_names),
        schemas=tuple(kept_schemas),
        schema_chars=schema_chars,
        excluded_tools=tuple(excluded),
        exclusion_reasons=sanitize_unicode(reasons),
        access_mode=mode,
        source="registry_availability_permission",
    )


def resolve_permission_tool_schemas(
    tool_specs: Mapping[str, Any] | Sequence[Any],
    tool_schemas: Sequence[dict[str, Any]],
    *,
    access_mode: str,
) -> tuple[dict[str, Any], ...]:
    """Return the complete registry-ordered tool universe allowed by access mode."""

    mode = get_agent_access_mode(access_mode)
    specs = _spec_mapping(tool_specs)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schema in tool_schemas:
        name = schema_tool_name(schema)
        spec = specs.get(name)
        if not name or name in seen or spec is None:
            continue
        if not evaluate_agent_tool_access(spec, access_mode=mode).allowed:
            continue
        seen.add(name)
        result.append(deepcopy(sanitize_unicode(schema)))
    return tuple(result)


def permission_tool_names(
    schemas: Sequence[dict[str, Any]],
) -> tuple[str, ...]:
    """Return names derived from one already-resolved permission universe."""

    return tuple(
        name
        for schema in schemas
        if (name := schema_tool_name(schema))
    )


def permission_tool_schema_chars(
    schemas: Sequence[dict[str, Any]],
) -> int:
    """Return the exact compact JSON size of one permission universe."""

    return _json_chars(list(schemas)) if schemas else 0


def initial_agent_turn_tools(surface: InitialToolSurfaceDecision) -> list[dict[str, Any]]:
    """Return only the real model-visible schemas for the initial agent turn."""

    return [deepcopy(schema) for schema in surface.schemas]


def _exclusion_reason(name: str, spec: Any, schema: dict[str, Any] | None, access_mode: str) -> str:
    if spec is None:
        return "not_registered"
    if schema is None:
        return "schema_missing"
    if str(getattr(spec, "provider", "") or "") != ToolProvider.LOCAL:
        return "non_local_provider"
    access = evaluate_agent_tool_access(spec, access_mode=access_mode)
    if not access.allowed:
        return (
            "read_only_side_effect"
            if access.code == "agent_access_mode_read_only"
            else access.code
        )
    return ""


def _spec_mapping(tool_specs: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]:
    if isinstance(tool_specs, Mapping):
        return {str(name): spec for name, spec in tool_specs.items() if str(name)}
    result: dict[str, Any] = {}
    for spec in tool_specs or []:
        name = str(getattr(spec, "name", "") or "")
        if name:
            result[name] = spec
    return result


def _json_chars(value: Any) -> int:
    return len(json.dumps(sanitize_unicode(value), ensure_ascii=False, separators=(",", ":"), default=str))


__all__ = [
    "InitialToolSurfaceDecision",
    "build_initial_tool_surface",
    "initial_agent_turn_tools",
    "permission_tool_names",
    "permission_tool_schema_chars",
    "resolve_permission_tool_schemas",
]
