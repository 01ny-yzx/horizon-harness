"""Tools for local document chunks."""

from __future__ import annotations

from typing import Any

from core.document_chunker import DocumentChunker
from core.document_loader import DocumentLoader
from core.document_store import DocumentStore, build_record_from_loaded_document
from core.file_access_policy import FileAccessPolicy
from core.workspace_runtime import get_document_dir


def _store() -> DocumentStore:
    return DocumentStore(document_dir=get_document_dir())


def list_chunks(document_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List chunk metadata with short previews."""

    return _store().list_chunks(document_id=document_id, limit=limit)


def get_chunk(chunk_id: str) -> dict[str, Any]:
    """Return one chunk with full text."""

    return _store().get_chunk(chunk_id)


def search_document_chunks(
    keyword: str,
    document_id: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search local document chunks by keyword."""

    return _store().find_chunks(keyword=keyword, document_id=document_id, limit=limit)


def clear_chunks() -> dict[str, Any]:
    """Clear all chunks."""

    return _store().clear_chunks()


def rebuild_chunks_for_document(
    document_id: str,
    chunk_size: int = 800,
    overlap: int = 120,
) -> dict[str, Any]:
    """Reload the original file path and rebuild chunks for a document."""

    store = _store()
    document_result = store.get_document(document_id)
    if not document_result.get("success"):
        return document_result
    document = document_result.get("data", {})
    if not isinstance(document, dict):
        return {"success": False, "error": "Document record is incomplete."}

    path = str(document.get("path", ""))
    decision = FileAccessPolicy().evaluate(path, operation="read", raw_requested_path=path)
    metadata = {
        "path": str(decision.resolved_path or path),
        "path_grounding": dict(decision.path_grounding or {}),
    }
    if not decision.allowed or not decision.resolved_path:
        return {
            "success": False,
            "error": decision.reason or "Document path access failed.",
            "error_code": decision.code,
            "data": decision.to_dict(),
            "metadata": metadata,
        }
    loader = DocumentLoader()
    loaded = loader.load_file(decision.resolved_path)
    if not loaded.get("success"):
        return {
            "success": False,
            "error": f"Cannot rebuild chunks because the original file cannot be loaded: {loaded.get('error')}",
            "metadata": metadata,
        }
    data = loaded.get("data", {})
    if not isinstance(data, dict):
        return {"success": False, "error": "DocumentLoader returned incomplete data.", "metadata": metadata}

    summary = loader.summarize_document(str(data.get("text", "")))
    record = build_record_from_loaded_document(data, summary)
    record.document_id = document_id
    store.add_document(record)
    chunks = DocumentChunker().chunk_text(
        text=str(data.get("text", "")),
        document_id=document_id,
        file_name=str(data.get("file_name", "")),
        path=str(data.get("path", "")),
        headings=summary.get("headings", []),
        chunk_size=chunk_size,
        overlap=overlap,
    )
    result = store.add_chunks(document_id, chunks)
    if not result.get("success"):
        result["metadata"] = metadata
        return result
    return {
        "success": True,
        "data": {
            "document_id": document_id,
            "path": path,
            "file_name": data.get("file_name", ""),
            "chunk_count": len(chunks),
            "chunk_size": chunk_size,
            "overlap": overlap,
        },
        "metadata": metadata,
    }


CHUNK_TOOLS = {
    "list_chunks": list_chunks,
    "get_chunk": get_chunk,
    "search_document_chunks": search_document_chunks,
    "clear_chunks": clear_chunks,
    "rebuild_chunks_for_document": rebuild_chunks_for_document,
}


CHUNK_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_chunks",
            "description": "List local document chunks with short previews. Does not return full chunk text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "Optional document id filter."},
                    "limit": {"type": "integer", "description": "Maximum chunks to list. Default 50.", "default": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chunk",
            "description": "Get one chunk by chunk_id, including full chunk text.",
            "parameters": {
                "type": "object",
                "properties": {"chunk_id": {"type": "string", "description": "Chunk id."}},
                "required": ["chunk_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_document_chunks",
            "description": "Keyword-search local document chunks. This is not embedding search or RAG.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword to search in chunk text, heading, and file name."},
                    "document_id": {"type": "string", "description": "Optional document id filter."},
                    "limit": {"type": "integer", "description": "Maximum matches. Default 5.", "default": 5},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_chunks",
            "description": "Clear all local document chunks from Chunk Store.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rebuild_chunks_for_document",
            "description": "Rebuild chunks for a loaded document by re-reading its original local path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "Document id to rebuild."},
                    "chunk_size": {"type": "integer", "description": "Chunk size in characters. Default 800.", "default": 800},
                    "overlap": {"type": "integer", "description": "Chunk overlap in characters. Default 120.", "default": 120},
                },
                "required": ["document_id"],
            },
        },
    },
]
