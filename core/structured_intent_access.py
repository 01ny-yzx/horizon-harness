"""Structured intent accessors.

These helpers read structured fields already projected onto TaskState,
TaskProfile, or ToolPlan. They deliberately do not parse
raw user text.
"""

from __future__ import annotations

from typing import Any


EXECUTION_VALIDATION_CAPABILITIES = {"validation", "python_sandbox", "shell_sandbox"}
EXECUTION_VALIDATION_TOOLS = {"sandbox_exec"}
DISABLED_TOOL_EXECUTION_MODES = frozenset(
    {
        "text_only",
        "simulate",
        "no_op",
    }
)


def tool_execution_enabled(
    *,
    tool_required: bool,
    execution_mode: str,
) -> bool:
    return bool(tool_required) and str(
        execution_mode or "normal"
    ) not in DISABLED_TOOL_EXECUTION_MODES


def structured_task_profile(task_state: Any) -> Any | None:
    return getattr(task_state, "task_profile", None)


def structured_tool_execution_enabled(
    task_state: Any,
) -> bool:
    profile = structured_task_profile(task_state)
    if profile is None:
        return True

    return tool_execution_enabled(
        tool_required=bool(
            getattr(
                profile,
                "tool_required",
                True,
            )
        ),
        execution_mode=str(
            getattr(
                profile,
                "execution_mode",
                "normal",
            )
            or "normal"
        ),
    )


def structured_tool_plan(task_state: Any) -> dict[str, Any]:
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    direct = metadata.get("tool_plan")
    if isinstance(direct, dict):
        return dict(direct)
    routing = metadata.get("capability_routing")
    if isinstance(routing, dict) and isinstance(routing.get("tool_plan"), dict):
        return dict(routing["tool_plan"])
    return {}


def structured_urls(task_state: Any) -> list[str]:
    profile = structured_task_profile(task_state)
    urls: list[str] = []
    if profile is None:
        return urls
    for item in getattr(profile, "provided_urls", []) or []:
        if isinstance(item, str) and item.strip() and item not in urls:
            urls.append(item)
    browser_url = getattr(profile, "browser_url", "") or ""
    if isinstance(browser_url, str) and browser_url.strip() and browser_url not in urls:
        urls.append(browser_url)
    return urls


def structured_file_output_required(task_state: Any) -> bool:
    if not structured_tool_execution_enabled(task_state):
        return False
    profile = structured_task_profile(task_state)
    return bool(profile and getattr(profile, "needs_file_output", False))


def structured_requested_execution_tools(task_state: Any) -> list[str]:
    if not structured_tool_execution_enabled(task_state):
        return []
    profile = structured_task_profile(task_state)
    if profile is None:
        return []
    requested = getattr(profile, "requested_tool_names", None) or ()
    result = [
        _base_tool_name(str(item or ""))
        for item in requested
        if _base_tool_name(str(item or "")) in EXECUTION_VALIDATION_TOOLS
    ]
    return list(dict.fromkeys(result))


def structured_validation_required(task_state: Any) -> bool:
    return bool(structured_validation_required_source(task_state))


def structured_file_output_validation_required(task_state: Any) -> bool:
    return bool(structured_file_output_required(task_state) and structured_validation_required(task_state))


def structured_validation_required_source(task_state: Any) -> str:
    if not structured_tool_execution_enabled(task_state):
        return ""
    profile = structured_task_profile(task_state)
    if bool(getattr(profile, "needs_validation", False)):
        return "task_profile_needs_validation"

    plan = structured_tool_plan(task_state)
    primary_capability = str(plan.get("primary_capability") or "")
    if primary_capability in EXECUTION_VALIDATION_CAPABILITIES:
        return "tool_plan_primary_capability"

    if _tool_plan_execution_tools(plan):
        return "tool_plan_execution_tool"

    if structured_file_output_required(task_state) and structured_requested_execution_tools(task_state):
        return "requested_execution_tool"

    return ""


def _tool_plan_execution_tools(plan: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    primary = str(plan.get("primary_tool") or "").strip()
    if primary:
        values.append(primary)
    for key in ("tool_priority", "supporting_tool_priority", "fallback_tool_priority"):
        items = plan.get(key)
        if isinstance(items, list):
            values.extend(str(item or "").strip() for item in items if str(item or "").strip())
    result = [_base_tool_name(item) for item in values if _base_tool_name(item) in EXECUTION_VALIDATION_TOOLS]
    return tuple(dict.fromkeys(result))


def _base_tool_name(tool_name: str) -> str:
    return tool_name.rsplit(".", 1)[-1].strip()


def structured_research_flags(task_state: Any) -> dict[str, Any]:
    profile = structured_task_profile(task_state)
    urls = structured_urls(task_state)
    if not structured_tool_execution_enabled(task_state):
        return {
            "tool_execution_enabled": False,
            "needs_web_search": False,
            "needs_fetch_url": False,
            "needs_browser": False,
            "provided_urls": urls,
            "research_intent": str(
                getattr(
                    profile,
                    "research_intent",
                    "",
                )
                or ""
            ),
            "tool_plan": {},
            "primary_capability": "",
            "primary_tool": "",
        }
    plan = structured_tool_plan(task_state)
    primary_capability = str(plan.get("primary_capability") or "")
    needs_fetch_url = bool(profile and getattr(profile, "needs_fetch_url", False))
    needs_browser = bool(profile and getattr(profile, "needs_browser", False))
    needs_web_search = bool(profile and getattr(profile, "needs_web_search", False))
    research_intent = str(getattr(profile, "research_intent", "none") if profile else "none") or "none"
    return {
        "tool_execution_enabled": True,
        "needs_web_search": needs_web_search,
        "needs_fetch_url": needs_fetch_url,
        "needs_browser": needs_browser,
        "provided_urls": urls,
        "research_intent": research_intent,
        "tool_plan": plan,
        "primary_capability": primary_capability,
        "primary_tool": str(plan.get("primary_tool") or ""),
    }


def structured_has_fetchable_url(task_state: Any) -> bool:
    return bool(structured_urls(task_state))


def structured_web_search_required(task_state: Any) -> bool:
    if not structured_tool_execution_enabled(task_state):
        return False
    flags = structured_research_flags(task_state)
    return bool(flags["needs_web_search"] or flags["primary_capability"] == "web_search")
