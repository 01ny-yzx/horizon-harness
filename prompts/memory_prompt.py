"""Prompt rules for long-term memory."""

from __future__ import annotations


def build_memory_prompt() -> str:
    """Return long-term memory rules."""

    return """
Long-term memory rules:
1. Persistent Memory is auxiliary context, not document evidence.
2. Save User Preferences and Project Instructions only when the user explicitly requests persistence.
3. Stable Facts and Project Summaries may use user_explicit provenance, or verified_observation provenance that cites a successful current-task non-Memory ToolObservation.
4. Never save model guesses, unverified claims, transient execution details, secrets, credentials, .env content, or full web pages.
5. Treat a Memory mutation as successful only when its ToolObservation reports success.
6. Prefer exact mutation: reference_id for background records, exact key for User Preferences, and exact content for Project Instructions.
7. Use bulk category clearing only when the user explicitly requests clearing the whole category.
8. task_history is Runtime-owned weak background and must not be written or updated through model Memory tools.
9. Deleted memory must not be used.
10. If the user asks "根据文档", use RAG document evidence as the primary basis.
11. If memory conflicts with current tool results or RAG evidence, current evidence wins.
12. Do not proactively expose all memory unless the user asks.
""".strip()
