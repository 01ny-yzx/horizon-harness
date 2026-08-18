"""Task profile schema used by structured intent and runtime planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


TaskType = Literal["coding", "research", "simple"]
BrowserAction = Literal["extract_text", "screenshot", "click", "form", "links", "none"]


@dataclass(frozen=True)
class TaskProfile:
    """Compact intent profile created before the Agent starts a task."""

    task_type: TaskType
    needs_web: bool
    has_url: bool
    has_search_engine_url: bool
    needs_code_edit: bool
    needs_validation: bool
    needs_git: bool
    user_intent_summary: str
    needs_document_load: bool = False
    document_paths: list[str] | None = None
    document_query: str = ""
    needs_document_retrieval: bool = False
    retrieval_keywords: list[str] | None = None
    needs_semantic_retrieval: bool = False
    needs_vector_search: bool = False
    retrieval_mode: Literal["keyword", "semantic", "hybrid", "none"] = "none"
    needs_rag: bool = False
    rag_query: str = ""
    rag_mode: Literal["semantic", "keyword", "hybrid", "none"] = "none"
    needs_research: bool = False
    needs_web_search: bool = False
    needs_fetch_url: bool = False
    needs_browser: bool = False
    browser_action: BrowserAction = "none"
    browser_url: str | None = None
    provided_urls: list[str] | None = None
    research_intent: str = "none"
    research_flow: list[str] | None = None
    needs_file_output: bool = False
    output_format: str = "unknown"
    output_target: str = "unknown"
    output_filename: str = ""
    requested_output_path: str = ""
    raw_requested_output_path: str = ""
    is_file_output_only: bool = False
    is_coding_task: bool = False
    workflow_kind: str = "simple"
    topic: str = ""
    filename_pattern: str = ""
    explicit_tool_intent: bool = False
    requested_tool_names: list[str] | None = None
    requested_tool_family: str = ""
    tool_intent_reason: str = ""
    coding_kind: str = ""
    coding_target_area: str = ""
    coding_likely_files: list[str] | None = None
    coding_required_checks: list[str] | None = None
    coding_action: str = ""
    profile_source: str = "safe_fallback"
    structured_intent_type: str = ""
    structured_task_type: str = ""
    structured_workflow_kind: str = ""
    side_effect_required: bool = True
    tool_required: bool = True
    execution_mode: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return asdict(self)

    def format_for_prompt(self) -> str:
        """Return a concise prompt-friendly summary."""

        return (
            "TaskProfile: "
            f"task_type={self.task_type}, "
            f"needs_web={self.needs_web}, "
            f"has_url={self.has_url}, "
            f"has_search_engine_url={self.has_search_engine_url}, "
            f"needs_code_edit={self.needs_code_edit}, "
            f"needs_validation={self.needs_validation}, "
            f"needs_git={self.needs_git}, "
            f"needs_document_load={self.needs_document_load}, "
            f"needs_document_retrieval={self.needs_document_retrieval}, "
            f"needs_semantic_retrieval={self.needs_semantic_retrieval}, "
            f"needs_vector_search={self.needs_vector_search}, "
            f"retrieval_mode={self.retrieval_mode}, "
            f"needs_rag={self.needs_rag}, "
            f"rag_mode={self.rag_mode}, "
            f"rag_query={self.rag_query or 'none'}, "
            f"needs_research={self.needs_research}, "
            f"needs_web_search={self.needs_web_search}, "
            f"needs_fetch_url={self.needs_fetch_url}, "
            f"needs_browser={self.needs_browser}, "
            f"browser_action={self.browser_action}, "
            f"browser_url={self.browser_url or 'none'}, "
            f"provided_urls={self.provided_urls or []}, "
            f"research_intent={self.research_intent}, "
            f"research_flow={self.research_flow or []}, "
            f"needs_file_output={self.needs_file_output}, "
            f"output_format={self.output_format}, "
            f"output_target={self.output_target}, "
            f"output_filename={self.output_filename or 'none'}, "
            f"requested_output_path={self.requested_output_path or 'none'}, "
            f"raw_requested_output_path={self.raw_requested_output_path or 'none'}, "
            f"is_file_output_only={self.is_file_output_only}, "
            f"is_coding_task={self.is_coding_task}, "
            f"workflow_kind={self.workflow_kind}, "
            f"topic={self.topic or 'none'}, "
            f"filename_pattern={self.filename_pattern or 'none'}, "
            f"explicit_tool_intent={self.explicit_tool_intent}, "
            f"side_effect_required={self.side_effect_required}, "
            f"tool_required={self.tool_required}, "
            f"execution_mode={self.execution_mode}, "
            f"requested_tool_names={self.requested_tool_names or []}, "
            f"requested_tool_family={self.requested_tool_family or 'none'}, "
            f"tool_intent_reason={self.tool_intent_reason or 'none'}, "
            f"coding_kind={self.coding_kind or 'none'}, "
            f"coding_target_area={self.coding_target_area or 'none'}, "
            f"coding_likely_files={self.coding_likely_files or []}, "
            f"coding_required_checks={self.coding_required_checks or []}, "
            f"coding_action={self.coding_action or 'none'}, "
            f"profile_source={self.profile_source or 'unknown'}, "
            f"structured_intent_type={self.structured_intent_type or 'none'}, "
            f"structured_task_type={self.structured_task_type or 'none'}, "
            f"structured_workflow_kind={self.structured_workflow_kind or 'none'}, "
            f"document_paths={self.document_paths or []}, "
            f"document_query={self.document_query or 'none'}, "
            f"retrieval_keywords={self.retrieval_keywords or []}, "
            f"intent={self.user_intent_summary}"
        )
