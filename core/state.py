"""In-memory task state for reliable Agent execution.

The state model is intentionally small.  It is not persisted to a database and
does not try to become a workflow engine.  Its job is to give the Agent and the
prompt a shared view of what has been planned, what has completed, what changed,
and whether validation really happened.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from core.task_profile import TaskProfile

StepStatus = Literal["pending", "running", "completed", "failed", "skipped"]


@dataclass
class PlanStep:
    """One step in a rule-based execution plan."""

    index: int
    name: str
    instruction: str
    status: StepStatus = "pending"
    evidence: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return asdict(self)


@dataclass
class TaskState:
    """State for one user task in one Agent run."""

    task_id: str
    user_goal: str
    task_type: str
    task_profile: TaskProfile | None
    current_phase: str
    plan: list[PlanStep]
    workflow_name: str = ""
    workflow_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    user_id: str = "default_user"
    project_id: str = "default_project"
    workspace_id: str = "default_user/default_project"
    current_step_index: int = 0
    completed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    created_outputs: list[dict[str, Any]] = field(default_factory=list)
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    start_cwd: str = field(default_factory=os.getcwd)
    current_cwd: str = field(default_factory=os.getcwd)
    consecutive_failures: int = 0
    is_finished: bool = False
    git_repo_root: str = ""
    git_status_before: dict[str, Any] | None = None
    git_status_after: dict[str, Any] | None = None
    git_diff_summary: str = ""
    reviewed_diff: bool = False
    suggested_commit_message: str = ""
    research_queries: list[str] = field(default_factory=list)
    fetched_urls: list[str] = field(default_factory=list)
    tool_failures: list[dict[str, Any]] = field(default_factory=list)
    memory_used: bool = False
    memory_saved: bool = False
    saved_memory_types: list[str] = field(default_factory=list)
    memory_delete_result: dict[str, Any] | None = None
    document_loaded: bool = False
    document_id: str = ""
    chunk_count: int = 0
    store_status: Any = ""
    loaded_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    document_load_status: str = ""
    loaded_documents: list[dict[str, Any]] = field(default_factory=list)
    document_load_failures: list[dict[str, Any]] = field(default_factory=list)
    chunk_retrieved: bool = False
    chunk_search_attempted: bool = False
    semantic_search_used: bool = False
    semantic_search_degraded: bool = False
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    chunk_retrieval_failures: list[dict[str, Any]] = field(default_factory=list)
    rag_used: bool = False
    rag_query: str = ""
    rag_mode: str = ""
    retrieval_degraded: bool = False
    retrieval_degraded_reason: str = ""
    rag_context_ready: bool = False
    rag_answer_grounded: bool = False
    rag_rewritten_query: str = ""
    rag_generated_queries: list[str] = field(default_factory=list)
    rag_evidence_count: int = 0
    rag_low_relevance_count: int = 0
    rag_enough_evidence: bool = False
    rag_evidence_reason: str = ""
    rag_low_relevance_chunks: list[dict[str, Any]] = field(default_factory=list)
    rag_retrieval_failures: list[dict[str, Any]] = field(default_factory=list)
    file_output_completed: bool = False
    file_output_result: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        user_goal: str,
        task_type: str,
        plan: list[PlanStep],
        task_profile: TaskProfile | None = None,
    ) -> "TaskState":
        """Create a new task state with a short random id."""

        return cls(
            task_id=uuid.uuid4().hex[:8],
            user_goal=user_goal,
            task_type=task_type,
            task_profile=task_profile,
            current_phase=plan[0].name if plan else "unknown",
            plan=plan,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "user_goal": self.user_goal,
            "task_type": self.task_type,
            "task_profile": self.task_profile.to_dict() if self.task_profile else None,
            "workflow_name": self.workflow_name,
            "workflow_kind": self.workflow_kind,
            "metadata": self.metadata,
            "current_phase": self.current_phase,
            "plan": [step.to_dict() for step in self.plan],
            "current_step_index": self.current_step_index,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "modified_files": self.modified_files,
            "output_files": self.output_files,
            "created_outputs": self.created_outputs,
            "validation_results": self.validation_results,
            "start_cwd": self.start_cwd,
            "current_cwd": self.current_cwd,
            "consecutive_failures": self.consecutive_failures,
            "is_finished": self.is_finished,
            "git_repo_root": self.git_repo_root,
            "git_status_before": self.git_status_before,
            "git_status_after": self.git_status_after,
            "git_diff_summary": self.git_diff_summary,
            "reviewed_diff": self.reviewed_diff,
            "suggested_commit_message": self.suggested_commit_message,
            "research_queries": self.research_queries,
            "fetched_urls": self.fetched_urls,
            "tool_failures": self.tool_failures,
            "memory_used": self.memory_used,
            "memory_saved": self.memory_saved,
            "saved_memory_types": self.saved_memory_types,
            "memory_delete_result": self.memory_delete_result,
            "document_loaded": self.document_loaded,
            "document_id": self.document_id,
            "chunk_count": self.chunk_count,
            "store_status": self.store_status,
            "loaded_count": self.loaded_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "document_load_status": self.document_load_status,
            "loaded_documents": self.loaded_documents,
            "document_load_failures": self.document_load_failures,
            "chunk_retrieved": self.chunk_retrieved,
            "chunk_search_attempted": self.chunk_search_attempted,
            "semantic_search_used": self.semantic_search_used,
            "semantic_search_degraded": self.semantic_search_degraded,
            "retrieved_chunks": self.retrieved_chunks,
            "chunk_retrieval_failures": self.chunk_retrieval_failures,
            "rag_used": self.rag_used,
            "rag_query": self.rag_query,
            "rag_mode": self.rag_mode,
            "retrieval_degraded": self.retrieval_degraded,
            "retrieval_degraded_reason": self.retrieval_degraded_reason,
            "rag_context_ready": self.rag_context_ready,
            "rag_answer_grounded": self.rag_answer_grounded,
            "rag_rewritten_query": self.rag_rewritten_query,
            "rag_generated_queries": self.rag_generated_queries,
            "rag_evidence_count": self.rag_evidence_count,
            "rag_low_relevance_count": self.rag_low_relevance_count,
            "rag_enough_evidence": self.rag_enough_evidence,
            "rag_evidence_reason": self.rag_evidence_reason,
            "rag_low_relevance_chunks": self.rag_low_relevance_chunks,
            "rag_retrieval_failures": self.rag_retrieval_failures,
            "file_output_completed": self.file_output_completed,
            "file_output_result": self.file_output_result,
        }

    def format_for_prompt(self) -> str:
        """Format state as a compact system note for the LLM."""

        lines = [
            "TaskState:",
            f"- task_id: {self.task_id}",
            f"- workspace: {self.workspace_id}",
            f"- user_goal: {self.user_goal}",
            f"- task_type: {self.task_type}",
            f"- workflow: {self.workflow_name or 'none'} kind={self.workflow_kind or 'none'}",
            f"- coding_context: {self._format_coding_context()}",
            f"- {self.task_profile.format_for_prompt() if self.task_profile else 'TaskProfile: none'}",
            f"- current_phase: {self.current_phase}",
            f"- current_cwd: {self.current_cwd}",
            f"- modified_files: {self.modified_files or 'none'}",
            f"- output_files: {self.output_files or 'none'}",
            f"- validation_results: {self._format_validation_summary()}",
            f"- git_repo_root: {self.git_repo_root or 'not detected'}",
            f"- git_has_changes: {self._format_git_changes()}",
            f"- reviewed_diff: {self.reviewed_diff}",
            f"- suggested_commit_message: {self.suggested_commit_message or 'none'}",
            f"- research_queries: {self.research_queries or 'none'}",
            f"- fetched_urls: {self.fetched_urls or 'none'}",
            f"- document_loaded: {self.document_loaded}",
            f"- loaded_documents: {self._format_loaded_documents()}",
            f"- document_load_failures: {self._format_document_load_failures()}",
            f"- chunk_retrieved: {self.chunk_retrieved}",
            f"- chunk_search_attempted: {self.chunk_search_attempted}",
            f"- semantic_search_used: {self.semantic_search_used}",
            f"- semantic_search_degraded: {self.semantic_search_degraded}",
            f"- retrieved_chunks: {self._format_retrieved_chunks()}",
            f"- chunk_retrieval_failures: {self._format_chunk_retrieval_failures()}",
            f"- rag_used: {self.rag_used}",
            f"- rag_mode: {self.rag_mode or 'none'}",
            f"- rag_query: {self.rag_query or 'none'}",
            f"- rag_rewritten_query: {self.rag_rewritten_query or 'none'}",
            f"- rag_generated_queries: {self.rag_generated_queries or 'none'}",
            f"- rag_context_ready: {self.rag_context_ready}",
            f"- rag_evidence_count: {self.rag_evidence_count}",
            f"- rag_low_relevance_count: {self.rag_low_relevance_count}",
            f"- rag_enough_evidence: {self.rag_enough_evidence}",
            f"- rag_evidence_reason: {self.rag_evidence_reason or 'none'}",
            f"- rag_retrieval_failures: {self._format_rag_retrieval_failures()}",
            f"- file_output_completed: {self.file_output_completed}",
            f"- file_output_result: {self._format_file_output_result()}",
            f"- retrieval_degraded: {self.retrieval_degraded} {self.retrieval_degraded_reason or ''}".rstrip(),
            f"- memory_used: {self.memory_used}",
            f"- memory_saved: {self.memory_saved}",
            f"- saved_memory_types: {self.saved_memory_types or 'none'}",
            f"- memory_delete_result: {self._format_memory_delete_result()}",
            f"- recent_tool_failures: {self._format_tool_failures()}",
            f"- consecutive_failures: {self.consecutive_failures}",
            "- plan:",
        ]
        for step in self.plan:
            detail = step.evidence or step.error or step.instruction
            lines.append(f"  {step.index}. {step.name} [{step.status}] - {detail}")
        return "\n".join(lines)

    def mark_step_completed(self, step_name: str, evidence: str = "") -> None:
        """Mark a plan step completed and advance the current pointer."""

        step = self._find_step(step_name)
        if step is None:
            return

        step.status = "completed"
        step.evidence = evidence
        step.error = ""
        if step.name not in self.completed_steps:
            self.completed_steps.append(step.name)
        if step.name in self.failed_steps:
            self.failed_steps.remove(step.name)
        self.current_step_index = min(step.index, len(self.plan) - 1)
        self._advance_to_next_pending()

    def mark_step_failed(self, step_name: str, error: str = "") -> None:
        """Mark a plan step failed without hiding previous evidence."""

        step = self._find_step(step_name)
        if step is None:
            return

        step.status = "failed"
        step.error = error
        if step.name not in self.failed_steps:
            self.failed_steps.append(step.name)
        self.current_step_index = step.index
        self.current_phase = step.name

    def record_modified_file(self, path: str) -> None:
        """Remember a modified file once."""

        if path and path not in self.modified_files:
            self.modified_files.append(path)

    def record_output_file(self, output: dict[str, Any]) -> None:
        """Remember a user-facing generated output file separately from code edits."""

        path = str(output.get("path") or "")
        if path and path not in self.output_files:
            self.output_files.append(path)
        compact = {
            "task_id": self.task_id,
            "path": path,
            "filename": output.get("filename", ""),
            "target_type": output.get("target_type", ""),
            "artifact_id": output.get("artifact_id", ""),
            "download_url": output.get("download_url", ""),
            "bytes": output.get("bytes", 0),
            "open_directory": output.get("open_directory", ""),
            "collision_resolved": bool(output.get("collision_resolved", False)),
            "original_path": output.get("original_path", ""),
        }
        key = compact["path"] or compact["download_url"] or compact["artifact_id"]
        existing = {item.get("path") or item.get("download_url") or item.get("artifact_id") for item in self.created_outputs}
        if key and key not in existing:
            self.created_outputs.append(compact)

    def record_file_output(self, observation: dict[str, Any]) -> None:
        """Remember a successful file output tool result."""

        data = observation.get("data", {})
        data = data if isinstance(data, dict) else {}
        self.file_output_completed = True
        self.file_output_result = {
            "task_id": self.task_id,
            "success": observation.get("success") is True,
            "requested_path": data.get("requested_path", ""),
            "path": data.get("path", ""),
            "filename": data.get("filename", ""),
            "target_type": data.get("target_type", ""),
            "artifact_id": data.get("artifact_id", ""),
            "download_url": data.get("download_url", ""),
            "bytes": data.get("bytes", 0),
            "content_preview": data.get("content_preview", ""),
            "content_hash": data.get("content_hash", ""),
            "output_format": data.get("output_format", ""),
            "open_directory": data.get("open_directory", ""),
            "open_directory_label": data.get("open_directory_label", ""),
            "open_directory_hint": data.get("open_directory_hint", ""),
            "collision_resolved": bool(data.get("collision_resolved", False)),
            "original_path": data.get("original_path", ""),
            "suggestion": data.get("suggestion", ""),
        }

    def record_validation(self, tool_name: str, observation: dict[str, Any]) -> None:
        """Remember one validation observation."""

        data = observation.get("data", {})
        data = data if isinstance(data, dict) else {}
        exit_code = data.get("exit_code", data.get("returncode"))
        self.validation_results.append(
            {
                "tool": tool_name,
                "success": observation.get("success") is True,
                "status": observation.get("status") or data.get("status"),
                "kind": observation.get("kind") or data.get("kind"),
                "returncode": exit_code,
                "exit_code": exit_code,
                "command": data.get("command", ""),
                "cwd": data.get("cwd", ""),
                "stdout_preview": str(data.get("stdout", ""))[:1000],
                "stderr_preview": str(data.get("stderr", ""))[:1000],
                "error": observation.get("error", ""),
            }
        )

    def record_research_query(self, query: str) -> None:
        """Remember one web research query."""

        if query and query not in self.research_queries:
            self.research_queries.append(query)

    def record_fetched_url(self, url: str) -> None:
        """Remember one fetched URL."""

        if url and url not in self.fetched_urls:
            self.fetched_urls.append(url)

    def record_tool_failure(self, tool_name: str, observation: dict[str, Any]) -> None:
        """Remember recent tool failures without storing huge payloads."""

        data = observation.get("data", {})
        data = data if isinstance(data, dict) else {}
        failure = {
            "tool": tool_name,
            "error": str(observation.get("error", ""))[:500],
            "error_code": str(observation.get("error_code") or data.get("error_code") or "")[:120],
            "recoverable": bool(observation.get("recoverable") is True or data.get("recoverable") is True),
            "recovery_reason": str(
                observation.get("recovery_reason") or data.get("recovery_reason") or ""
            )[:240],
            "url": str(data.get("url", ""))[:500],
            "blocked_url": str(data.get("blocked_url", ""))[:500],
            "code": str(data.get("code", ""))[:120],
            "boundary": str(data.get("boundary", ""))[:120],
            "blocked_by": str(data.get("blocked_by", ""))[:120],
            "blocked_tool": str(data.get("blocked_tool", ""))[:120],
            "reason": str(data.get("reason", ""))[:240],
            "path": str(data.get("path", ""))[:500],
            "requested_path": str(data.get("requested_path", ""))[:500],
            "raw_path": str(data.get("raw_path", ""))[:500],
            "embedded_tool_risk_operation": str(data.get("embedded_tool_risk_operation", ""))[:120],
            "execution_payload_risk": str(data.get("execution_payload_risk", ""))[:120],
        }
        if data.get("kind") == "execution" or observation.get("kind") == "execution":
            failure.update(
                {
                    "kind": "execution",
                    "status": str(data.get("status") or observation.get("status") or "")[:80],
                    "message": str(observation.get("message") or data.get("message") or "")[:500],
                    "command": str(data.get("command") or "")[:1000],
                    "cwd": str(data.get("cwd") or "")[:500],
                    "stdout": str(data.get("stdout") or "")[:2000],
                    "stderr": str(data.get("stderr") or "")[:2000],
                    "exit_code": data.get("exit_code", data.get("returncode")),
                }
            )
        self.tool_failures.append(failure)
        self.tool_failures = self.tool_failures[-10:]

    def record_loaded_document(self, document: dict[str, Any]) -> None:
        """Remember one loaded document without storing preview text."""

        compact = {
            "document_id": document.get("document_id", ""),
            "path": document.get("path", ""),
            "file_name": document.get("file_name", ""),
            "extension": document.get("extension", ""),
            "truncated": bool(document.get("truncated", False)),
        }
        if not compact["document_id"] and not compact["path"] and not compact["file_name"]:
            return
        key = compact["document_id"] or compact["path"] or compact["file_name"]
        existing_keys = {
            item.get("document_id") or item.get("path") or item.get("file_name")
            for item in self.loaded_documents
        }
        if key not in existing_keys:
            self.loaded_documents.append(compact)
            self.loaded_documents = self.loaded_documents[-20:]
        complete_storage = (
            document.get("document_stored") is True
            and document.get("chunks_stored") is True
            and "chunk_count" in document
            and str(document.get("status") or "success").lower() == "success"
        )
        if compact["document_id"] and complete_storage:
            self.document_loaded = True
            self.document_id = str(compact["document_id"])
            self.chunk_count = int(document.get("chunk_count") or 0)
            self.store_status = document.get("store_status") or ""
            self.document_load_status = str(document.get("status") or "success")

    def record_document_load_result(self, tool_name: str, data: dict[str, Any], *, success: bool) -> None:
        """Record only evidence emitted by real document-load tools."""

        if tool_name == "load_document":
            self.record_loaded_document(data)
            if not self.document_load_status:
                self.document_load_status = str(data.get("status") or ("success" if success else "failed"))
            return
        if tool_name != "load_documents_from_directory":
            return
        documents = [item for item in data.get("documents", []) if isinstance(item, dict)]
        for item in documents:
            self.record_loaded_document(item)
        self.loaded_count = int(data.get("loaded_count") or 0)
        self.failed_count = int(data.get("failed_count") or 0)
        self.skipped_count = int(data.get("skipped_count") or 0)
        self.chunk_count = int(data.get("total_chunks_created") or 0)
        self.document_load_status = str(data.get("status") or ("success" if success else "failed"))
        self.document_loaded = (
            self.loaded_count == len(documents)
            and self.loaded_count > 0
            and self.failed_count == 0
            and all(
                item.get("document_stored") is True
                and item.get("chunks_stored") is True
                and "chunk_count" in item
                and str(item.get("status") or "success").lower() == "success"
                for item in documents
            )
        )

    def record_document_load_failure(self, tool_name: str, observation: dict[str, Any]) -> None:
        """Remember a document loading failure without storing large payloads."""

        data = observation.get("data", {})
        data = data if isinstance(data, dict) else {}
        self.document_load_failures.append(
            {
                "tool": tool_name,
                "error": str(observation.get("error", ""))[:500],
                "path": str(data.get("path") or data.get("requested_path") or data.get("raw_path") or "")[:500],
                "code": str(data.get("code", ""))[:120],
            }
        )
        self.document_load_failures = self.document_load_failures[-10:]

    def record_retrieved_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Remember retrieved chunk metadata without storing full chunk text."""

        self.chunk_search_attempted = True
        for chunk in chunks:
            compact = {
                "chunk_id": chunk.get("chunk_id", ""),
                "document_id": chunk.get("document_id", ""),
                "file_name": chunk.get("file_name", ""),
                "heading": chunk.get("heading", ""),
                "score": chunk.get("rerank_score", chunk.get("score", 0)),
                "similarity_score": chunk.get("semantic_score", chunk.get("score", 0)),
                "evidence_quality": chunk.get("evidence_quality", ""),
                "matched_keywords": chunk.get("matched_keywords", []),
            }
            if not compact["chunk_id"]:
                continue
            existing_ids = {item.get("chunk_id") for item in self.retrieved_chunks}
            if compact["chunk_id"] not in existing_ids:
                self.retrieved_chunks.append(compact)
        self.retrieved_chunks = self.retrieved_chunks[-20:]
        self.chunk_retrieved = bool(self.retrieved_chunks)

    def record_rag_retrieval(
        self,
        query: str,
        mode: str,
        matches: list[dict[str, Any]],
        degraded: bool = False,
        degraded_reason: str = "",
        rewritten_query: str = "",
        generated_queries: list[str] | None = None,
        low_relevance_chunks: list[dict[str, Any]] | None = None,
        enough_evidence: bool | None = None,
        evidence_reason: str = "",
    ) -> None:
        """Remember RAG retrieval metadata without full chunk text."""

        self.rag_used = True
        self.rag_query = query
        self.rag_mode = mode
        self.retrieval_degraded = degraded
        self.retrieval_degraded_reason = degraded_reason[:500]
        self.rag_rewritten_query = rewritten_query[:500]
        self.rag_generated_queries = [str(item)[:200] for item in (generated_queries or [])][:4]
        self.rag_low_relevance_chunks = [self._compact_chunk(item) for item in (low_relevance_chunks or [])][:10]
        self.rag_low_relevance_count = len(low_relevance_chunks or [])
        self.rag_evidence_count = len(matches)
        self.rag_enough_evidence = bool(matches) if enough_evidence is None else bool(enough_evidence)
        self.rag_evidence_reason = evidence_reason[:500]
        if not matches:
            self.retrieved_chunks = []
            self.chunk_retrieved = False
            self.chunk_search_attempted = True
            self.rag_context_ready = False
            self.rag_answer_grounded = False
            return
        self.record_retrieved_chunks(matches)
        self.rag_context_ready = bool(self.retrieved_chunks)
        self.rag_answer_grounded = self.rag_context_ready and self.rag_enough_evidence

    @staticmethod
    def _compact_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_id": chunk.get("chunk_id", ""),
            "document_id": chunk.get("document_id", ""),
            "file_name": chunk.get("file_name", ""),
            "heading": chunk.get("heading", ""),
            "score": chunk.get("score", chunk.get("rerank_score", 0)),
            "evidence_quality": chunk.get("evidence_quality", ""),
        }

    def record_chunk_retrieval_failure(self, tool_name: str, observation: dict[str, Any]) -> None:
        """Remember chunk retrieval failures compactly."""

        self.chunk_retrieval_failures.append(
            {
                "tool": tool_name,
                "error": str(observation.get("error", ""))[:500],
            }
        )
        self.chunk_retrieval_failures = self.chunk_retrieval_failures[-10:]

    def record_rag_retrieval_failure(self, tool_name: str, observation: dict[str, Any]) -> None:
        """Remember RAG retrieval failures compactly."""

        self.rag_retrieval_failures.append(
            {
                "tool": tool_name,
                "error": str(observation.get("error", ""))[:500],
            }
        )
        self.rag_retrieval_failures = self.rag_retrieval_failures[-5:]

    def has_successful_validation(self) -> bool:
        """Return True when at least one validation succeeded."""

        return any(result.get("success") is True for result in self.validation_results)

    def latest_validation_failed(self) -> bool:
        """Return True when the latest validation exists and failed."""

        if not self.validation_results:
            return False
        return self.validation_results[-1].get("success") is False

    def latest_validation_passed(self) -> bool:
        """Return True when the latest validation exists and passed."""

        if not self.validation_results:
            return False
        return self.validation_results[-1].get("success") is True

    def coding_completion_ready(self) -> bool:
        """Return True when a coding edit has modified, validated, and reviewed required diff."""

        if self.task_type != "coding":
            return False
        if not self.modified_files:
            return False
        if not self.latest_validation_passed():
            return False
        if self.git_repo_root and not self.reviewed_diff:
            return False
        return True

    def git_available(self) -> bool:
        """Return True when a Git repo root was detected."""

        return bool(self.git_repo_root)

    def unfinished_steps(self) -> list[str]:
        """Return names of steps not completed."""

        return [step.name for step in self.plan if step.status != "completed"]

    def _find_step(self, step_name: str) -> PlanStep | None:
        """Find a step by name."""

        return next((step for step in self.plan if step.name == step_name), None)

    def _advance_to_next_pending(self) -> None:
        """Move current phase to the next pending step, if any."""

        for step in self.plan:
            if step.status == "pending":
                self.current_step_index = step.index
                self.current_phase = step.name
                return
        self.current_phase = "final_summary"

    def _format_validation_summary(self) -> str:
        """Return a short validation summary."""

        if not self.validation_results:
            return "none"
        latest = self.validation_results[-1]
        status = "passed" if latest.get("success") else "failed"
        return f"{status} via {latest.get('tool')}"

    def _format_git_changes(self) -> str:
        """Return a short Git status summary."""

        status = self.git_status_after or self.git_status_before
        if not status:
            return "unknown"
        if isinstance(status, dict):
            return str(status.get("has_changes", "unknown"))
        return "unknown"

    def _format_tool_failures(self) -> str:
        """Return a compact recent failure summary."""

        if not self.tool_failures:
            return "none"
        latest = self.tool_failures[-1]
        return f"{latest.get('tool')}: {latest.get('error')}"

    def _format_loaded_documents(self) -> str:
        """Return a compact loaded document summary for prompts."""

        if not self.loaded_documents:
            return "none"
        names = [str(item.get("file_name") or item.get("path") or item.get("document_id")) for item in self.loaded_documents]
        return f"count={len(self.loaded_documents)} files={names[:10]}"

    def _format_document_load_failures(self) -> str:
        """Return a compact document failure summary."""

        if not self.document_load_failures:
            return "none"
        latest = self.document_load_failures[-1]
        return f"{latest.get('tool')}: {latest.get('error')}"

    def _format_retrieved_chunks(self) -> str:
        """Return compact retrieved chunk ids for prompts."""

        if not self.retrieved_chunks:
            return "none"
        ids = [str(item.get("chunk_id", "")) for item in self.retrieved_chunks[:10]]
        return f"count={len(self.retrieved_chunks)} chunk_ids={ids}"

    def _format_chunk_retrieval_failures(self) -> str:
        """Return compact chunk retrieval failure summary."""

        if not self.chunk_retrieval_failures:
            return "none"
        latest = self.chunk_retrieval_failures[-1]
        return f"{latest.get('tool')}: {latest.get('error')}"

    def _format_rag_retrieval_failures(self) -> str:
        """Return compact RAG failure summary."""

        if not self.rag_retrieval_failures:
            return "none"
        latest = self.rag_retrieval_failures[-1]
        return f"{latest.get('tool')}: {latest.get('error')}"

    def _format_memory_delete_result(self) -> str:
        """Return a compact memory deletion summary."""

        if not self.memory_delete_result:
            return "none"
        data = self.memory_delete_result.get("data", {})
        if not isinstance(data, dict):
            return str(self.memory_delete_result.get("success"))
        return f"deleted={data.get('deleted', 0)} preferences={data.get('deleted_preferences', [])}"

    def _format_file_output_result(self) -> str:
        if not self.file_output_result:
            return "none"
        return (
            f"target_type={self.file_output_result.get('target_type') or 'unknown'} "
            f"path={self.file_output_result.get('path') or 'none'} "
            f"download_url={self.file_output_result.get('download_url') or 'none'}"
        )

    def _format_coding_context(self) -> str:
        if not self.metadata:
            return "none"
        intent = self.metadata.get("coding_intent")
        if not isinstance(intent, dict):
            return "none"
        likely_files = self.metadata.get("likely_files") or intent.get("likely_files") or []
        required_checks = self.metadata.get("required_checks") or intent.get("required_checks") or []
        return (
            f"kind={intent.get('kind', 'unknown')} "
            f"target_area={self.metadata.get('target_area') or intent.get('target_area', 'unknown')} "
            f"likely_files={likely_files} "
            f"required_checks={required_checks}"
        )
