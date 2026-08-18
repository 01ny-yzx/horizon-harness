"""Context fusion for RAG, persistent memory, and task state."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from core.persistent_memory import PersistentMemory


SECRET_RE = re.compile(r"(?i)(api[_-]?key|password|passwd|token|secret)\s*[:=]|sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,}")


@dataclass
class MemoryEvidence:
    """A compact memory item that may be used as background."""

    memory_type: str
    content: str
    source: str = ""
    updated_at: str = ""
    confidence: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContextFusionEngine:
    """Build a bounded context note with explicit priority and evidence types."""

    priority_order = [
        "current_observation",
        "rag_evidence",
        "current_user_input",
        "task_state",
        "project_memory",
        "user_memory",
        "recent_task_history",
    ]

    def build_fused_context(
        self,
        user_input: str,
        task_state: Any,
        persistent_memory: PersistentMemory,
        rag_result: dict[str, Any] | None = None,
        max_chars: int = 6000,
    ) -> dict[str, Any]:
        """Return a bounded fused context with sections and warnings."""

        warnings: list[str] = []
        sections = {
            "current_task": self._current_task_section(user_input, task_state),
            "rag_evidence": self._rag_section(task_state, rag_result),
            "persistent_memory": self._persistent_memory_section(persistent_memory),
            "recent_tasks": self._recent_tasks_section(persistent_memory),
        }
        context_text = self.format_for_prompt(sections, max_chars=max_chars)
        if SECRET_RE.search(context_text):
            warnings.append("Sensitive-looking token removed from fused context.")
            context_text = SECRET_RE.sub("[REDACTED]", context_text)
        return {
            "success": True,
            "data": {
                "context_text": context_text[:max_chars],
                "sections": sections,
                "priority_order": list(self.priority_order),
                "warnings": warnings,
            },
        }

    def format_for_prompt(self, sections: dict[str, str] | None = None, max_chars: int = 6000, **kwargs: Any) -> str:
        """Format sections as a system note."""

        if sections is None:
            built = self.build_fused_context(max_chars=max_chars, **kwargs)
            sections = built.get("data", {}).get("sections", {})
        lines = [
            "Fused Context:",
            "Priority: current Observation > RAG document evidence > user input > TaskState > project memory > user memory > recent task history.",
            "Rules: RAG evidence is document evidence. Persistent Memory is background/preference unless the user asks about memory. Task history is weak background only.",
        ]
        for name in ["current_task", "rag_evidence", "persistent_memory", "recent_tasks"]:
            content = (sections.get(name, "") if isinstance(sections, dict) else "").strip()
            if content:
                lines.extend([f"\n[{name}]", content])
        return "\n".join(lines)[:max_chars]

    @staticmethod
    def memory_evidence(persistent_memory: PersistentMemory) -> list[dict[str, Any]]:
        """Extract compact memory evidence items."""

        items: list[MemoryEvidence] = []
        user = persistent_memory.get_user_memory()
        preferences = user.get("preferences", {}) if isinstance(user, dict) else {}
        if isinstance(preferences, dict):
            for key, value in list(preferences.items())[:8]:
                data = value if isinstance(value, dict) else {"value": value}
                items.append(MemoryEvidence("user_preference", f"{key}: {data.get('value', '')}", data.get("source", ""), data.get("updated_at", ""), "medium"))
        facts = user.get("stable_facts", []) if isinstance(user, dict) else []
        if isinstance(facts, list):
            for fact in facts[-5:]:
                if isinstance(fact, dict):
                    items.append(MemoryEvidence("stable_fact", str(fact.get("content", "")), str(fact.get("source", "")), str(fact.get("updated_at", "")), "medium"))
        project = persistent_memory.get_project_memory()
        projects = project.get("projects", []) if isinstance(project, dict) else []
        if isinstance(projects, list):
            for item in projects[-3:]:
                if isinstance(item, dict):
                    items.append(MemoryEvidence("project_summary", str(item.get("summary", "")), str(item.get("source", "")), str(item.get("updated_at", "")), "medium"))
        for task in persistent_memory.get_prompt_safe_recent_tasks(limit=3):
            items.append(
                MemoryEvidence(
                    "task_history",
                    persistent_memory.safe_task_prompt_summary(task),
                    str(task.get("source", "")),
                    str(task.get("updated_at", "")),
                    "low",
                )
            )
        return [item.to_dict() for item in items if item.content]

    @staticmethod
    def _current_task_section(user_input: str, task_state: Any) -> str:
        profile = getattr(task_state, "task_profile", None)
        return "\n".join(
            [
                f"user_input: {user_input}",
                f"task_type: {getattr(task_state, 'task_type', '')}",
                f"needs_rag: {getattr(profile, 'needs_rag', False) if profile else False}",
                f"rag_used: {getattr(task_state, 'rag_used', False)}",
                f"rag_enough_evidence: {getattr(task_state, 'rag_enough_evidence', False)}",
            ]
        )

    @staticmethod
    def _rag_section(task_state: Any, rag_result: dict[str, Any] | None) -> str:
        evidence = []
        if rag_result:
            evidence = rag_result.get("evidence_chunks", [])
        if not evidence:
            evidence = getattr(task_state, "retrieved_chunks", [])
        if not evidence:
            return "No sufficient RAG document evidence is available."
        lines = ["Document evidence chunks:"]
        for item in evidence[:5]:
            lines.append(
                "- "
                f"file={item.get('file_name', '')} chunk_id={item.get('chunk_id', '')} "
                f"heading={item.get('heading', '')} quality={item.get('evidence_quality', '')} "
                f"score={item.get('rerank_score', item.get('score', ''))}"
            )
            text = str(item.get("text") or item.get("chunk_preview") or item.get("text_preview") or "")[:500]
            if text:
                lines.append(f"  excerpt: {text}")
        return "\n".join(lines)

    def _persistent_memory_section(self, persistent_memory: PersistentMemory) -> str:
        evidence = self.memory_evidence(persistent_memory)
        if not evidence:
            return "Persistent Memory: none"
        lines = ["Memory background (not document evidence):"]
        for item in evidence:
            if item.get("memory_type") == "task_history":
                continue
            lines.append(f"- [{item.get('memory_type')}; confidence={item.get('confidence')}] {item.get('content')[:300]}")
        return "\n".join(lines[:12])

    @staticmethod
    def _recent_tasks_section(persistent_memory: PersistentMemory) -> str:
        tasks = persistent_memory.get_prompt_safe_recent_tasks(limit=3)
        if not tasks:
            return "Recent task history: none"
        lines = ["Recent task history (weak background only):"]
        for task in tasks:
            summary = persistent_memory.safe_task_prompt_summary(task)
            lines.append(f"- [{task.get('task_type')}] {summary[:240]} ({task.get('result')})")
        return "\n".join(lines)

def compact_memory_summary(persistent_memory: PersistentMemory, max_chars: int = 1200) -> str:
    """Return a compact memory summary suitable for query rewrite."""

    text = persistent_memory.format_for_prompt(max_chars=max_chars, include_task_history=False, mode="rag")
    return SECRET_RE.sub("[REDACTED]", text)[:max_chars]
