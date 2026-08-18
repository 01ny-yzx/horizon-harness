"""Prompt rules for long-term memory."""

from __future__ import annotations


def build_memory_prompt() -> str:
    """Return long-term memory rules."""

    return """
Long-term memory rules:
1. Persistent Memory is auxiliary context, not document evidence.
2. User preferences only affect answer style unless the user explicitly asks what is remembered.
3. Project memory can help interpret intent and expand RAG queries.
4. task_history is weak background and must not be used as a factual basis.
5. Deleted memory must not be used.
6. If the user asks "根据文档", use RAG document evidence as the primary basis.
7. If memory conflicts with current tool results, current tool results win.
8. If memory conflicts with RAG evidence, RAG evidence wins.
9. Do not store API keys, passwords, tokens, .env content, private secrets, or full web pages.
10. Memory deletion or clearing must use a currently supplied memory tool and be confirmed by a successful Observation.
11. Do not claim memory deletion succeeded unless the tool reports deleted > 0.
12. Do not proactively expose all memory unless the user asks.
""".strip()
