"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_document_prompt() -> str:
    return """Document work:
- Choose current file/document/retrieval tools according to the request and their supported formats. A filename alone does not automatically require RAG.
- Read relevant source content before making claims about it. If only a bounded excerpt was returned, do not claim full-document analysis.
- When the user restricts the answer to documents, use their evidence and state missing information without outside-knowledge filler.
- Prefer a useful available retrieval method if embeddings are unavailable. Do not invent chunks, filenames, scores or citations.
- Keep searches scoped to relevant sources; respect private stores and unsupported formats according to runtime policy.
- Present useful findings and source references naturally, without a mandatory evidence section or raw vector output.""".strip()
