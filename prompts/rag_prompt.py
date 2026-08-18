"""Prompt rules for Retrieval Augmented Generation."""

from __future__ import annotations


def build_rag_prompt() -> str:
    """Return RAG behavior rules."""

    return """
RAG v1 rules:
0. "README.md" alone is a local file path reference, not automatically a RAG request.
1. RAG means Retrieval Augmented Generation: retrieve local document chunks, then answer from those chunks.
2. Retrieval observations are the primary evidence. Current Observation beats long-term memory.
3. If the user asks to answer from loaded documents, local documents, README, project docs, or a knowledge base, use the currently supplied retrieval tools as needed.
4. Base document-grounded claims on actual successful retrieval observations.
5. If embeddings are unavailable, use keyword retrieval and clearly mention the degradation.
6. RAG answers must be based on evidence_chunks, not all retrieved matches.
7. low_relevance_chunks cannot be used as evidence and must not be cited as support.
8. citations can only come from evidence_chunks.
9. Do not invent document facts, chunk_id, file_name, heading, or chunk citations.
10. RAG answers must be complete and based on evidence_chunks.
11. Present evidence naturally in the answer. Include all necessary evidence and source context directly in the answer without forcing a fixed evidence or source section.
12. If enough_evidence=false, say "已加载文档中没有足够相关内容" and do not add model-knowledge filler.
13. If query rewrite generated multiple queries, do not show all queries unless DEBUG_MODE or the user asks for diagnostics.
14. Do not output embedding arrays.
15. Do not claim the full document was analyzed unless the relevant chunks were actually loaded and retrieved.
16. If the user asks to answer from loaded documents, do not claim retrieval succeeded without an actual successful observation.
17. If RAG context is ready, answer from evidence_chunks.
18. If RAG retrieval completed but found no sufficient evidence, say the loaded documents do not contain enough relevant content.
19. Do not treat a Validation Guard reminder as a final answer.
""".strip()
