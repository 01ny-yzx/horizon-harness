"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_rag_prompt() -> str:
    return """Retrieval-grounded answers:
- Use available retrieval tools when the user requests document/knowledge-base evidence. A local filename can instead be a direct file-read task.
- Base claims and citations on relevant retrieved evidence, not low-relevance matches or unverified memory.
- If evidence is insufficient, say what is missing. Do not invent document facts, chunk IDs, filenames, headings or sources.
- Do not claim full-document analysis from retrieved fragments. Use direct reading where available and appropriate.
- Answer naturally with necessary source context; hide embeddings, raw ranking diagnostics and internal query rewrites unless asked for diagnostics.""".strip()
