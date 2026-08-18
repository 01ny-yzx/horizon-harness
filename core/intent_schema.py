"""Intent Schema v1 data structures and normalization helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from core.coding_intent_resolution import normalize_coding_action
from core.output_path_resolver import path_has_filename


INTENT_SCHEMA_VERSION = "intent_schema_v1"

TASK_TYPES = {
    "simple",
    "research",
    "coding",
    "rag",
    "file_output",
    "tool_use",
    "unknown",
}

INTENT_TYPES = {
    "simple_answer",
    "file_read",
    "file_write",
    "document_read",
    "document_load",
    "research",
    "research_file_output",
    "browser_extract",
    "browser_links",
    "browser_screenshot",
    "database_read",
    "database_schema",
    "database_query",
    "rag",
    "coding",
    "dangerous_or_write_blocked",
    "unknown",
}

WORKFLOW_KINDS = {
    "simple",
    "research",
    "coding",
    "rag",
    "file_output_only",
    "research_file_output",
    "unknown",
}

TOOL_FAMILIES = {
    "file_read",
    "filesystem",
    "file_write",
    "document",
    "document_read",
    "document_load",
    "browser",
    "web",
    "database",
    "database_read",
    "database_schema",
    "database_query",
    "rag",
    "coding",
    "validation",
    "git",
    "mcp",
    "mcp_tool",
    "plugin",
    "marketplace",
}

TOOL_NAMES = {
    "read_file",
    "list_files",
    "read_document",
    "load_document",
    "load_documents_from_directory",
    "write_file",
    "replace_in_file",
    "web_search",
    "fetch_url",
    "browser_extract",
    "browser",
    "browser_extract_text",
    "browser_links",
    "browser_screenshot",
    "database_list_tables",
    "database_describe_table",
    "database_select_query",
    "database",
    "database_delete_rows",
    "drop_table",
    "rag_query",
    "sandbox_exec",
}

BROWSER_ACTIONS = {"extract_text", "links", "screenshot", "none"}
EXECUTION_MODES = {"normal", "text_only", "simulate", "no_op"}


@dataclass
class IntentFlags:
    needs_web: bool = False
    needs_fetch_url: bool = False
    needs_browser: bool = False
    needs_document_load: bool = False
    needs_document_retrieval: bool = False
    needs_rag: bool = False
    needs_code_edit: bool = False
    needs_validation: bool = False
    needs_git: bool = False
    needs_file_output: bool = False
    explicit_tool_intent: bool = False
    side_effect_required: bool = True
    tool_required: bool = True
    execution_mode: str = "normal"


@dataclass
class IntentArtifact:
    output_format: str | None = None
    output_target: str | None = None
    output_filename: str | None = None
    requested_output_path: str | None = None
    raw_requested_output_path: str | None = None
    content_goal: str | None = None
    filename_pattern: str | None = None


@dataclass
class IntentRetrieval:
    document_paths: list[str] = field(default_factory=list)
    document_query: str | None = None
    retrieval_keywords: list[str] = field(default_factory=list)
    retrieval_mode: str | None = None
    rag_query: str | None = None
    rag_mode: str | None = None


@dataclass
class IntentWeb:
    provided_urls: list[str] = field(default_factory=list)
    browser_action: str | None = None
    browser_url: str | None = None
    research_intent: str | None = None
    research_flow: str | None = None
    has_search_engine_url: bool = False


@dataclass
class IntentDatabase:
    sql: str | None = None
    table: str | None = None
    operation: str | None = None


@dataclass
class IntentTools:
    requested_tool_names: list[str] = field(default_factory=list)
    requested_tool_family: str | None = None
    allowed_tool_families: list[str] = field(default_factory=list)
    blocked_tool_families: list[str] = field(default_factory=list)


@dataclass
class IntentCoding:
    kind: str | None = None
    target_area: str | None = None
    likely_files: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)
    action: str | None = None


@dataclass
class IntentContinuation:
    is_followup: bool = False
    inherits_previous_task: bool = False
    inherited_fields: list[str] = field(default_factory=list)


@dataclass
class IntentRisk:
    may_modify_files: bool = False
    may_use_network: bool = False
    may_execute_code: bool = False
    requires_user_confirmation: bool = False
    prompt_injection: bool = False
    prompt_exfiltration: bool = False
    safety_notes: list[str] = field(default_factory=list)


@dataclass
class IntentClassification:
    schema_version: str = INTENT_SCHEMA_VERSION
    intent_type: str = "unknown"
    task_type: str = "unknown"
    workflow_kind: str = "unknown"
    user_goal: str = ""
    summary: str = ""
    topic: str = ""
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    flags: IntentFlags = field(default_factory=IntentFlags)
    artifact: IntentArtifact = field(default_factory=IntentArtifact)
    retrieval: IntentRetrieval = field(default_factory=IntentRetrieval)
    web: IntentWeb = field(default_factory=IntentWeb)
    database: IntentDatabase = field(default_factory=IntentDatabase)
    coding: IntentCoding = field(default_factory=IntentCoding)
    continuation: IntentContinuation = field(default_factory=IntentContinuation)
    tools: IntentTools = field(default_factory=IntentTools)
    risk: IntentRisk = field(default_factory=IntentRisk)
    raw_extra: dict[str, object] = field(default_factory=dict)


STANDARD_FIELDS = {
    "schema_version",
    "intent_type",
    "task_type",
    "workflow_kind",
    "user_goal",
    "summary",
    "topic",
    "confidence",
    "reasons",
    "flags",
    "artifact",
    "retrieval",
    "web",
    "database",
    "coding",
    "continuation",
    "tools",
    "risk",
    "raw_extra",
}


def is_valid_task_type(value: object) -> bool:
    return isinstance(value, str) and value in TASK_TYPES


def is_valid_intent_type(value: object) -> bool:
    return isinstance(value, str) and value in INTENT_TYPES


def is_valid_workflow_kind(value: object) -> bool:
    return isinstance(value, str) and value in WORKFLOW_KINDS


def clamp_confidence(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    return False


def bool_value_default(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return bool_value(value)


def _execution_mode(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in EXECUTION_MODES:
            return normalized
    return "normal"


def default_intent() -> IntentClassification:
    return IntentClassification()


def normalize_intent_payload(raw: object) -> IntentClassification:
    try:
        if not isinstance(raw, dict):
            return default_intent()

        return IntentClassification(
            schema_version=_string_value(raw.get("schema_version"), INTENT_SCHEMA_VERSION),
            intent_type=_intent_type(raw.get("intent_type")),
            task_type=_task_type(raw.get("task_type")),
            workflow_kind=_workflow_kind(raw.get("workflow_kind")),
            user_goal=_string_value(raw.get("user_goal"), ""),
            summary=_string_value(raw.get("summary"), ""),
            topic=_string_value(raw.get("topic"), ""),
            confidence=clamp_confidence(raw.get("confidence", 0.0)),
            reasons=string_list(raw.get("reasons")),
            flags=_normalize_flags(raw.get("flags")),
            artifact=_normalize_artifact(raw.get("artifact")),
            retrieval=_normalize_retrieval(raw.get("retrieval")),
            web=_normalize_web(raw.get("web")),
            database=_normalize_database(raw.get("database")),
            coding=_normalize_coding(raw.get("coding")),
            continuation=_normalize_continuation(raw.get("continuation")),
            tools=_normalize_tools(raw.get("tools")),
            risk=_normalize_risk(raw.get("risk")),
            raw_extra={
                key: _json_friendly(value)
                for key, value in raw.items()
                if isinstance(key, str) and key not in STANDARD_FIELDS
            },
        )
    except Exception:
        return default_intent()


def intent_to_dict(intent: IntentClassification) -> dict[str, object]:
    return _json_friendly(asdict(intent) if is_dataclass(intent) else intent)  # type: ignore[return-value]


def is_file_output_intent(intent: IntentClassification) -> bool:
    if not intent_allows_real_side_effects(intent):
        return False
    return bool(
        intent.flags.needs_file_output
        or intent.intent_type in {"file_write", "research_file_output"}
        or intent.task_type == "file_output"
        or intent.workflow_kind in {"file_output_only", "research_file_output"}
    )


def intent_allows_real_side_effects(intent: IntentClassification) -> bool:
    return bool(
        intent.flags.side_effect_required
        and intent.flags.tool_required
        and intent.flags.execution_mode not in {"text_only", "simulate", "no_op"}
    )


def file_output_parameter_error(intent: IntentClassification) -> str | None:
    if not is_file_output_intent(intent):
        return None
    if intent.artifact.output_filename:
        return None
    if path_has_filename(intent.artifact.requested_output_path):
        return None
    return "incomplete_file_output_parameters"


def _task_type(value: object) -> str:
    return value if is_valid_task_type(value) else "unknown"


def _intent_type(value: object) -> str:
    return value if is_valid_intent_type(value) else "unknown"


def _workflow_kind(value: object) -> str:
    return value if is_valid_workflow_kind(value) else "unknown"


def _string_value(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_flags(value: object) -> IntentFlags:
    if not isinstance(value, dict):
        return IntentFlags()
    return IntentFlags(
        needs_web=bool_value(value.get("needs_web")),
        needs_fetch_url=bool_value(value.get("needs_fetch_url")),
        needs_browser=bool_value(value.get("needs_browser")),
        needs_document_load=bool_value(value.get("needs_document_load")),
        needs_document_retrieval=bool_value(value.get("needs_document_retrieval")),
        needs_rag=bool_value(value.get("needs_rag")),
        needs_code_edit=bool_value(value.get("needs_code_edit")),
        needs_validation=bool_value(value.get("needs_validation")),
        needs_git=bool_value(value.get("needs_git")),
        needs_file_output=bool_value(value.get("needs_file_output")),
        explicit_tool_intent=bool_value(value.get("explicit_tool_intent")),
        side_effect_required=bool_value_default(value.get("side_effect_required"), True),
        tool_required=bool_value_default(value.get("tool_required"), True),
        execution_mode=_execution_mode(value.get("execution_mode")),
    )


def _normalize_artifact(value: object) -> IntentArtifact:
    if not isinstance(value, dict):
        return IntentArtifact()
    return IntentArtifact(
        output_format=_string_or_none(value.get("output_format")),
        output_target=_string_or_none(value.get("output_target")),
        output_filename=_string_or_none(value.get("output_filename")),
        requested_output_path=_string_or_none(value.get("requested_output_path")),
        raw_requested_output_path=_string_or_none(value.get("raw_requested_output_path") or value.get("original_requested_path")),
        content_goal=_string_or_none(value.get("content_goal")),
        filename_pattern=_string_or_none(value.get("filename_pattern")),
    )


def _normalize_retrieval(value: object) -> IntentRetrieval:
    if not isinstance(value, dict):
        return IntentRetrieval()
    return IntentRetrieval(
        document_paths=string_list(value.get("document_paths")),
        document_query=_string_or_none(value.get("document_query")),
        retrieval_keywords=string_list(value.get("retrieval_keywords")),
        retrieval_mode=_string_or_none(value.get("retrieval_mode")),
        rag_query=_string_or_none(value.get("rag_query")),
        rag_mode=_string_or_none(value.get("rag_mode")),
    )


def _normalize_web(value: object) -> IntentWeb:
    if not isinstance(value, dict):
        return IntentWeb()
    return IntentWeb(
        provided_urls=string_list(value.get("provided_urls")),
        browser_action=_browser_action(value.get("browser_action")),
        browser_url=_string_or_none(value.get("browser_url")),
        research_intent=_string_or_none(value.get("research_intent")),
        research_flow=_string_or_none(value.get("research_flow")),
        has_search_engine_url=bool_value(value.get("has_search_engine_url")),
    )


def _normalize_database(value: object) -> IntentDatabase:
    if not isinstance(value, dict):
        return IntentDatabase()
    return IntentDatabase(
        sql=_string_or_none(value.get("sql")),
        table=_string_or_none(value.get("table")),
        operation=_string_or_none(value.get("operation")),
    )


def _normalize_coding(value: object) -> IntentCoding:
    if not isinstance(value, dict):
        return IntentCoding()
    return IntentCoding(
        kind=_string_or_none(value.get("kind")),
        target_area=_string_or_none(value.get("target_area")),
        likely_files=string_list(value.get("likely_files")),
        required_checks=string_list(value.get("required_checks")),
        action=normalize_coding_action(value.get("action")),
    )


def _normalize_continuation(value: object) -> IntentContinuation:
    if not isinstance(value, dict):
        return IntentContinuation()
    return IntentContinuation(
        is_followup=bool_value(value.get("is_followup")),
        inherits_previous_task=bool_value(value.get("inherits_previous_task")),
        inherited_fields=string_list(value.get("inherited_fields")),
    )


def _normalize_tools(value: object) -> IntentTools:
    if not isinstance(value, dict):
        return IntentTools()
    return IntentTools(
        requested_tool_names=_known_tool_names(value.get("requested_tool_names")),
        requested_tool_family=_known_tool_family(value.get("requested_tool_family")),
        allowed_tool_families=_known_tool_families(value.get("allowed_tool_families")),
        blocked_tool_families=_known_tool_families(value.get("blocked_tool_families")),
    )


def _normalize_risk(value: object) -> IntentRisk:
    if not isinstance(value, dict):
        return IntentRisk()
    return IntentRisk(
        may_modify_files=bool_value(value.get("may_modify_files")),
        may_use_network=bool_value(value.get("may_use_network")),
        may_execute_code=bool_value(value.get("may_execute_code")),
        requires_user_confirmation=bool_value(value.get("requires_user_confirmation")),
        prompt_injection=bool_value(value.get("prompt_injection")),
        prompt_exfiltration=bool_value(value.get("prompt_exfiltration")),
        safety_notes=string_list(value.get("safety_notes")),
    )


def _browser_action(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in BROWSER_ACTIONS else None


def _known_tool_family(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in TOOL_FAMILIES else None


def _known_tool_families(value: object) -> list[str]:
    return [family for family in (_known_tool_family(item) for item in string_list(value)) if family]


def _known_tool_names(value: object) -> list[str]:
    names: list[str] = []
    for item in string_list(value):
        normalized = item.strip().lower()
        if normalized in TOOL_NAMES and normalized not in names:
            names.append(normalized)
    return names


def _json_friendly(value: Any) -> Any:
    if is_dataclass(value):
        return _json_friendly(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_friendly(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_friendly(item) for item in value]
    if isinstance(value, tuple):
        return [_json_friendly(item) for item in value]
    if isinstance(value, (str, bool, float, int)) or value is None:
        return value
    return repr(value)
