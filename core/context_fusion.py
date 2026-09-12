"""Context fusion for RAG, persistent memory, and task state."""

from __future__ import annotations

import json
import re
from typing import Any

from core.persistent_memory import PersistentMemory


SECRET_RE = re.compile(r"(?i)(api[_-]?key|password|passwd|token|secret)\s*[:=]|sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,}")


class ContextFusionEngine:
    """Build a bounded context note with explicit priority and evidence types."""

    priority_order = [
        "current_observation",
        "rag_evidence",
        "current_user_input",
        "task_state",
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
            "Priority: current Observation > RAG document evidence > user input > TaskState.",
            "Rules: RAG evidence is document evidence. Explicit persistent guidance is supplied separately at request time.",
        ]
        for name in ["current_task", "rag_evidence", "persistent_memory", "recent_tasks"]:
            content = (sections.get(name, "") if isinstance(sections, dict) else "").strip()
            if content:
                lines.extend([f"\n[{name}]", content])
        return "\n".join(lines)[:max_chars]

    @staticmethod
    def memory_evidence(persistent_memory: PersistentMemory) -> list[dict[str, Any]]:
        """Keep explicit persistent guidance out of fused model context."""

        loaded = persistent_memory.load_all()
        if not loaded.get("success"):
            return []
        return []

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
        self.memory_evidence(persistent_memory)
        return ""

    @staticmethod
    def _recent_tasks_section(persistent_memory: PersistentMemory) -> str:
        del persistent_memory
        return ""

def compact_memory_summary(persistent_memory: PersistentMemory, max_chars: int = 1200) -> str:
    """Return a compact memory summary suitable for query rewrite."""

    loaded = persistent_memory.load_all()
    if not loaded.get("success"):
        return ""
    text = persistent_memory.format_instruction_context(max_chars=max_chars)
    return SECRET_RE.sub("[REDACTED]", text)[:max_chars]
