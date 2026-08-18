"""Prompt rules for local Document Loader, Chunking, and RAG tasks."""

from __future__ import annotations


def build_document_prompt() -> str:
    """Return local document prompt rules."""

    return """
Document and RAG rules:
0. "README.md" alone is a local file path reference, not automatically a RAG request.
1. Paths and queries are parameters only. Choose among the currently supplied document and file tools as needed.
2. Local document tasks do not require external web tools.
3. Do not recurse through directories unless the user asks.
4. Document-grounded answers require successful relevant observations.
5. If embeddings are unavailable, use another currently available retrieval method when useful and explain the degradation.
6. Answer document questions only from retrieved chunks. Do not say content is in the document unless a retrieved chunk supports it.
7. If no relevant chunks are found, say "我没有在已加载文档中找到相关内容".
8. Do not invent chunk_id, file_name, heading, score, or evidence. Present document evidence naturally from retrieved chunks, and include all necessary evidence directly in the answer without forcing a fixed evidence section.
9. Do not output embedding arrays.
10. Do not claim the full text was analyzed unless the relevant document was loaded and chunks were retrieved.
11. Do not read .env, secret files, memory_store, document_store, vector_store, .venv, caches, or unsupported binary formats.
12. Current supported file types are .txt, .md, .json, .csv, .py, .html, and .htm. PDF, DOCX, PPTX, images, and OCR are not supported in Document Loader v1.
""".strip()
