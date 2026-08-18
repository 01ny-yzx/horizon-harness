"""Tools for embedding chunks and searching the local vector store."""

from __future__ import annotations

from typing import Any

from config.settings import settings
from core.document_store import DocumentStore
from core.embedding_provider import EmbeddingProvider
from core.rate_limit import RateLimiter
from core.vector_store import VectorStore
from core.workspace_runtime import get_current_workspace, get_document_dir, get_vector_dir


def _document_store() -> DocumentStore:
    return DocumentStore(document_dir=get_document_dir())


def _vector_store() -> VectorStore:
    return VectorStore(vector_dir=get_vector_dir())


def get_embedding_status() -> dict[str, Any]:
    """Return embedding provider and vector store status."""

    status = EmbeddingProvider().get_status()
    vector_status = _vector_store().get_status()
    data = status.get("data", {}) if status.get("success") else {}
    if isinstance(data, dict):
        data["vector_store"] = vector_status.get("data", {})
    return status


def embed_chunk(chunk_id: str) -> dict[str, Any]:
    """Embed one chunk and upsert it into VectorStore."""

    provider = EmbeddingProvider()
    status = provider.get_status().get("data", {})
    if not isinstance(status, dict) or not status.get("available"):
        return {"success": False, "error": str(status.get("reason", "Embedding provider is unavailable."))}

    chunk_result = _document_store().get_chunk(chunk_id)
    if not chunk_result.get("success"):
        return chunk_result
    chunk = chunk_result.get("data", {})
    if not isinstance(chunk, dict):
        return {"success": False, "error": "Chunk record is incomplete."}

    embedding_result = provider.embed_text(str(chunk.get("text", "")))
    if not embedding_result.get("success"):
        return embedding_result
    embedding_data = embedding_result.get("data", {})
    if not isinstance(embedding_data, dict):
        return {"success": False, "error": "Embedding result is incomplete."}
    if not embedding_data.get("cached"):
        quota = RateLimiter().check_and_increment(get_current_workspace().user_id, "embedding")
        if not quota.get("allowed"):
            return {"success": False, "error": "Daily embedding limit exceeded.", "data": {"rate_limit": quota}}

    record = _vector_record_from_chunk(chunk, embedding_data)
    upsert = _vector_store().upsert_vector(record)
    if not upsert.get("success"):
        return upsert
    return {"success": True, "data": {"vector": upsert.get("data", {}).get("vector", {})}}


def embed_document_chunks(document_id: str) -> dict[str, Any]:
    """Embed all chunks for one document, skipping unchanged vectors."""

    document_store = _document_store()
    chunks_result = document_store.list_chunks(document_id=document_id, limit=10_000)
    if not chunks_result.get("success"):
        return chunks_result
    all_chunks = [
        chunk
        for chunk in document_store._load_chunks()  # noqa: SLF001 - tool needs full chunk text.
        if chunk.get("document_id") == document_id
    ]
    return _embed_chunks(all_chunks)


def embed_all_chunks(limit: int | None = None) -> dict[str, Any]:
    """Embed current chunks with a bounded default limit."""

    safe_limit = _safe_limit(limit, default=100, maximum=1000)
    chunks = _document_store()._load_chunks()[:safe_limit]  # noqa: SLF001 - tool needs full chunk text.
    return _embed_chunks(chunks)


def semantic_search_chunks(query: str, top_k: int = 5) -> dict[str, Any]:
    """Embed a query and search vectors by cosine similarity."""

    provider = EmbeddingProvider()
    status = provider.get_status().get("data", {})
    if not isinstance(status, dict) or not status.get("available"):
        return {"success": False, "error": str(status.get("reason", "Embedding provider is unavailable.")), "data": {"fallback": "keyword"}}
    embedding_result = provider.embed_text(query)
    if not embedding_result.get("success"):
        return embedding_result
    embedding_data = embedding_result.get("data", {})
    query_embedding = embedding_data.get("embedding", []) if isinstance(embedding_data, dict) else []
    search = _vector_store().similarity_search(
        query_embedding=query_embedding,
        top_k=top_k,
        threshold=settings.vector_similarity_threshold,
    )
    if not search.get("success"):
        return search
    matches = _attach_chunk_previews(search.get("data", {}).get("matches", []))
    data = dict(search.get("data", {}))
    data["matches"] = matches
    return {"success": True, "data": data}


def hybrid_search_chunks(query: str, keyword: str | None = None, top_k: int = 5) -> dict[str, Any]:
    """Combine semantic search and keyword chunk search with simple scoring."""

    semantic = semantic_search_chunks(query=query, top_k=top_k)
    keyword_term = keyword or query
    keyword_result = _document_store().find_chunks(keyword=keyword_term, limit=top_k)
    keyword_chunks = []
    if keyword_result.get("success"):
        keyword_chunks = keyword_result.get("data", {}).get("chunks", [])

    semantic_matches = []
    semantic_available = semantic.get("success") is True
    fallback_reason = ""
    if semantic_available:
        semantic_matches = semantic.get("data", {}).get("matches", [])
    else:
        fallback_reason = str(semantic.get("error", "Embedding unavailable; keyword search used."))

    merged: dict[str, dict[str, Any]] = {}
    for item in semantic_matches:
        chunk_id = str(item.get("chunk_id", ""))
        merged[chunk_id] = dict(item)
        merged[chunk_id]["semantic_score"] = float(item.get("score", 0.0))
        merged[chunk_id]["keyword_score"] = 0.0
    max_keyword_score = max([float(item.get("score", 0.0)) for item in keyword_chunks] or [1.0])
    for item in keyword_chunks:
        chunk_id = str(item.get("chunk_id", ""))
        existing = merged.setdefault(chunk_id, _chunk_match_from_keyword(item))
        existing["keyword_score"] = float(item.get("score", 0.0)) / max_keyword_score
        existing.setdefault("semantic_score", 0.0)
    for item in merged.values():
        item["score"] = round(float(item.get("semantic_score", 0.0)) + float(item.get("keyword_score", 0.0)), 6)
    matches = sorted(merged.values(), key=lambda item: -float(item.get("score", 0.0)))[: _safe_limit(top_k, 5, 50)]
    return {
        "success": True,
        "data": {
            "query": query,
            "keyword": keyword_term,
            "semantic_available": semantic_available,
            "fallback_reason": fallback_reason,
            "count": len(matches),
            "matches": matches,
        },
    }


def list_vectors(limit: int = 50) -> dict[str, Any]:
    """List vector metadata without embeddings."""

    return _vector_store().list_vectors(limit=limit)


def clear_vectors() -> dict[str, Any]:
    """Clear vector store."""

    return _vector_store().clear_vectors()


def _embed_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    provider = EmbeddingProvider()
    status = provider.get_status().get("data", {})
    if not isinstance(status, dict) or not status.get("available"):
        return {"success": False, "error": str(status.get("reason", "Embedding provider is unavailable."))}

    vector_store = _vector_store()
    created = updated = skipped = failed = 0
    failures = []
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id", ""))
        existing = vector_store.get_vector(chunk_id)
        existing_data = existing.get("data", {}) if existing.get("success") else {}
        if isinstance(existing_data, dict) and existing_data.get("content_hash") == chunk.get("content_hash"):
            skipped += 1
            continue
        embedding_result = provider.embed_text(str(chunk.get("text", "")))
        if not embedding_result.get("success"):
            failed += 1
            failures.append({"chunk_id": chunk_id, "error": embedding_result.get("error", "")})
            continue
        embedding_data = embedding_result.get("data", {})
        if not isinstance(embedding_data, dict):
            failed += 1
            failures.append({"chunk_id": chunk_id, "error": "Embedding result is incomplete."})
            continue
        if not embedding_data.get("cached"):
            quota = RateLimiter().check_and_increment(get_current_workspace().user_id, "embedding")
            if not quota.get("allowed"):
                failed += 1
                failures.append({"chunk_id": chunk_id, "error": "Daily embedding limit exceeded."})
                continue
        record = _vector_record_from_chunk(chunk, embedding_data)
        existed = existing.get("success") is True
        upsert = vector_store.upsert_vector(record)
        if upsert.get("success"):
            updated += 1 if existed else 0
            created += 0 if existed else 1
        else:
            failed += 1
            failures.append({"chunk_id": chunk_id, "error": upsert.get("error", "")})
    return {
        "success": True,
        "data": {
            "processed": len(chunks),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "failures": failures[:20],
        },
    }


def _vector_record_from_chunk(chunk: dict[str, Any], embedding_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "document_id": chunk.get("document_id", ""),
        "file_name": chunk.get("file_name", ""),
        "path": chunk.get("path", ""),
        "heading": chunk.get("heading", ""),
        "embedding": embedding_data.get("embedding", []),
        "embedding_dim": embedding_data.get("embedding_dim", 0),
        "embedding_model": embedding_data.get("embedding_model", ""),
        "content_hash": chunk.get("content_hash", ""),
    }


def _attach_chunk_previews(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    store = _document_store()
    results = []
    for item in matches:
        chunk = store.get_chunk(str(item.get("chunk_id", "")))
        data = chunk.get("data", {}) if chunk.get("success") else {}
        enriched = dict(item)
        if isinstance(data, dict):
            enriched["chunk_preview"] = str(data.get("text", ""))[:500]
        results.append(enriched)
    return results


def _chunk_match_from_keyword(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "document_id": chunk.get("document_id", ""),
        "file_name": chunk.get("file_name", ""),
        "path": chunk.get("path", ""),
        "heading": chunk.get("heading", ""),
        "chunk_preview": str(chunk.get("text", ""))[:500],
    }


def _safe_limit(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


VECTOR_TOOLS = {
    "get_embedding_status": get_embedding_status,
    "embed_chunk": embed_chunk,
    "embed_document_chunks": embed_document_chunks,
    "embed_all_chunks": embed_all_chunks,
    "semantic_search_chunks": semantic_search_chunks,
    "hybrid_search_chunks": hybrid_search_chunks,
    "list_vectors": list_vectors,
    "clear_vectors": clear_vectors,
}


VECTOR_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_embedding_status",
            "description": "Check whether semantic embedding is enabled and available.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embed_chunk",
            "description": "Generate an embedding for one chunk and store it locally. Does not return the embedding array.",
            "parameters": {"type": "object", "properties": {"chunk_id": {"type": "string"}}, "required": ["chunk_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embed_document_chunks",
            "description": "Generate embeddings for all chunks of one document.",
            "parameters": {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embed_all_chunks",
            "description": "Generate embeddings for current chunks with a safe limit. Default limit 100.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 100}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_chunks",
            "description": "Semantic search over local chunk vectors. Requires embedding provider and vectors.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hybrid_search_chunks",
            "description": "Hybrid semantic plus keyword chunk search. Falls back to keyword when embedding is unavailable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "keyword": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_vectors",
            "description": "List vector metadata without embedding arrays.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 50}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_vectors",
            "description": "Clear all local vector records.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
