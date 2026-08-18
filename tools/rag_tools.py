"""Tools for local RAG v1 status and retrieval."""

from __future__ import annotations

from typing import Any

from config.settings import settings
from core.cache import CacheManager
from core.document_store import DocumentStore
from core.embedding_provider import EmbeddingProvider
from core.rag import RAGEngine
from core.vector_store import VectorStore
from core.workspace_runtime import get_current_workspace, get_document_dir, get_vector_dir


def _document_store() -> DocumentStore:
    return DocumentStore(document_dir=get_document_dir())


def _vector_store() -> VectorStore:
    return VectorStore(vector_dir=get_vector_dir())


def rag_query(query: str, mode: str = "hybrid", top_k: int = 5, persistent_memory_context: str | None = None) -> dict[str, Any]:
    """Run RAG retrieval and return safe context preview plus citations."""

    if mode not in {"semantic", "keyword", "hybrid"}:
        mode = "hybrid"
    cache = CacheManager()
    workspace = get_current_workspace()
    cache_key = cache.make_key("rag", workspace.user_id, workspace.project_id, query, mode, top_k, _vector_fingerprint())
    cached = cache.get("rag", cache_key)
    if isinstance(cached, dict):
        cached_data = dict(cached)
        cached_data["cached"] = True
        return {"success": True, "data": cached_data}
    pipeline = RAGEngine(document_store=_document_store()).run_pipeline(query=query, mode=mode, top_k=top_k, persistent_memory_context=persistent_memory_context)
    pipeline_data = pipeline.to_dict()
    evidence_package = pipeline.evidence_package
    data = dict(evidence_package.diagnostics)
    data.update(
        {
            "query": pipeline.query,
            "mode": pipeline.mode,
            "degraded": bool(pipeline.metadata.get("degraded")),
            "degraded_reason": str(pipeline.metadata.get("degraded_reason", "")),
            "matches": evidence_package.evidence_chunks,
            "context_text": pipeline.final_context,
            "context_preview": pipeline.final_context[:1200],
            "citations": evidence_package.citations,
            "rewritten_query": pipeline.rewrite.rewritten_query,
            "keywords": pipeline.rewrite.keywords,
            "generated_queries": pipeline.rewrite.generated_queries,
            "evidence_chunks": evidence_package.evidence_chunks,
            "low_relevance_chunks": evidence_package.low_relevance_chunks,
            "enough_evidence": evidence_package.enough_evidence,
            "evidence_reason": evidence_package.evidence_reason,
            "top_score": evidence_package.top_score,
            "rerank_applied": bool(evidence_package.diagnostics.get("rerank_applied")),
            "retrieval_diagnostics": pipeline.metadata.get("retrieval_diagnostics", {}),
            "rewrite": pipeline_data["rewrite"],
            "retrieval_plan": pipeline_data["retrieval_plan"],
            "evidence_package": pipeline_data["evidence_package"],
            "low_evidence_decision": pipeline_data["low_evidence_decision"],
            "citation_guard": pipeline_data["citation_guard"],
            "final_context": pipeline.final_context,
            "pipeline_status": pipeline.metadata.get("pipeline_status", "low_evidence_fallback"),
        }
    )
    data["low_relevance_chunks"] = [_compact_low_relevance(item) for item in data.get("low_relevance_chunks", [])]
    if isinstance(data.get("evidence_package"), dict):
        data["evidence_package"]["low_relevance_chunks"] = data["low_relevance_chunks"]
    diagnostics = data.get("retrieval_diagnostics", {})
    data["memory_context_used"] = bool(diagnostics.get("memory_context_used"))
    data["memory_keywords_used"] = diagnostics.get("memory_keywords_used", [])
    data["fused_context_ready"] = False
    data["cached"] = False
    if data.get("query") and not data.get("error"):
        cache.set("rag", cache_key, data, ttl_seconds=settings.rag_cache_ttl_seconds)
    return {"success": True, "data": data}


def get_rag_status() -> dict[str, Any]:
    """Return whether the local RAG pipeline has enough data to run."""

    document_store = _document_store()
    documents = document_store.list_documents().get("data", {})
    chunks = document_store.list_chunks(limit=1).get("data", {})
    vectors = _vector_store().get_status().get("data", {})
    embedding = EmbeddingProvider().get_status().get("data", {})
    documents_count = int(documents.get("documents_count", 0) or 0) if isinstance(documents, dict) else 0
    chunks_count = int(chunks.get("chunks_count", 0) or 0) if isinstance(chunks, dict) else 0
    vectors_count = int(vectors.get("vectors_count", 0) or 0) if isinstance(vectors, dict) else 0
    embedding_available = bool(embedding.get("available")) if isinstance(embedding, dict) else False
    if chunks_count <= 0:
        rag_ready = False
        reason = "No document chunks are available. Load documents and build chunks first."
    elif embedding_available and vectors_count <= 0:
        rag_ready = True
        reason = "Chunks are available; semantic search needs vectors, but keyword fallback can run."
    else:
        rag_ready = True
        reason = "RAG retrieval can run."
    return {
        "success": True,
        "data": {
            "documents_count": documents_count,
            "chunks_count": chunks_count,
            "vectors_count": vectors_count,
            "embedding_available": embedding_available,
            "rag_ready": rag_ready,
            "reason": reason,
        },
    }


def clear_rag_context() -> dict[str, Any]:
    """Signal that the current session RAG context should be cleared."""

    return {"success": True, "data": {"cleared": True, "note": "RAG context note can be cleared in session memory."}}


RAG_TOOLS = {
    "rag_query": rag_query,
    "get_rag_status": get_rag_status,
    "clear_rag_context": clear_rag_context,
}


RAG_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "rag_query",
            "description": "Run local RAG retrieval over loaded document chunks. Returns matches, citations, and a context preview without embeddings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["semantic", "keyword", "hybrid"], "default": "hybrid"},
                    "top_k": {"type": "integer", "default": 5},
                    "persistent_memory_context": {"type": "string", "description": "Optional compact memory summary for query rewrite."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rag_status",
            "description": "Check local RAG readiness: documents, chunks, vectors, and embedding availability.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_rag_context",
            "description": "Clear only the current session RAG context note. Does not delete documents, chunks, or vectors.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _compact_low_relevance(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": item.get("chunk_id", ""),
        "document_id": item.get("document_id", ""),
        "file_name": item.get("file_name", ""),
        "heading": item.get("heading", ""),
        "score": item.get("score", 0),
        "evidence_quality": item.get("evidence_quality", "low"),
        "chunk_preview": str(item.get("chunk_preview", ""))[:240],
    }


def _vector_fingerprint() -> dict[str, Any]:
    store = _vector_store()
    vectors = store.load()
    updated = sorted(str(item.get("updated_at", "")) for item in vectors if isinstance(item, dict) and item.get("updated_at"))
    hashes = sorted(str(item.get("content_hash", "")) for item in vectors if isinstance(item, dict) and item.get("content_hash"))
    return {
        "vectors_count": len(vectors),
        "last_updated": updated[-1] if updated else "",
        "content_hash_tail": hashes[-5:],
    }
