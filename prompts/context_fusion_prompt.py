"""Prompt rules for fused context."""

from __future__ import annotations


def build_context_fusion_prompt() -> str:
    """Return context fusion rules."""

    return """
Context Fusion rules:
1. Priority is current Observation > RAG document evidence > current user input > project memory > user memory > recent task history.
2. RAG evidence is document evidence. Persistent Memory is background unless the user asks about memory.
3. User preferences affect answer style; they are not project facts.
4. Project memory and stable facts may help interpret intent, but current document evidence overrides them.
5. task_history is weak background only and must not be treated as current user preference or strong evidence.
6. Do not cite memory as chunk_id/file_name/heading. Do not fabricate sources.
7. If memory conflicts with current Observation or RAG evidence, follow the current Observation or RAG evidence and mention the conflict only when useful.
8. If the user says "根据文档", answer from RAG document evidence first. If no document evidence exists, say so before mentioning any memory background.
""".strip()
