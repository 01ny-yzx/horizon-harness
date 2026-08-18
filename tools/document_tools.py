"""Tools for loading and managing local document metadata."""

from __future__ import annotations

from typing import Any

from core.document_chunker import DocumentChunker
from core.document_loader import DocumentLoader
from core.document_store import DocumentStore, build_record_from_loaded_document
from core.file_access_policy import FileAccessDecision, FileAccessPolicy
from core.workspace_runtime import get_document_dir


PREVIEW_CHARS = 2000


def _store() -> DocumentStore:
    return DocumentStore(document_dir=get_document_dir())


def _document_read_decision(path: str, raw_requested_path: str | None = None) -> FileAccessDecision:
    return FileAccessPolicy().evaluate(
        path,
        operation="read",
        raw_requested_path=raw_requested_path or path,
    )


def _document_execution_metadata(decision: FileAccessDecision) -> dict[str, Any]:
    return {
        "path": str(decision.resolved_path or decision.requested_path or ""),
        "path_grounding": dict(decision.path_grounding or {}),
    }


def _document_access_failure(decision: FileAccessDecision) -> dict[str, Any]:
    data = {
        "operation": decision.operation,
        "scope": decision.scope,
        "requested_path": decision.requested_path,
        "resolved_path": decision.resolved_path or "",
        "code": decision.code,
        "reason": decision.reason,
        "path_grounding": dict(decision.path_grounding or {}),
    }
    return {
        "success": False,
        "error": decision.reason or "Document path access failed.",
        "error_code": decision.code,
        "data": data,
        "metadata": _document_execution_metadata(decision),
    }


def _with_document_metadata(result: dict[str, Any], decision: FileAccessDecision) -> dict[str, Any]:
    payload = dict(result)
    payload["metadata"] = _document_execution_metadata(decision)
    return payload


def load_document(
    path: str,
    create_chunks: bool = True,
    chunk_size: int = 800,
    overlap: int = 120,
    raw_requested_path: str | None = None,
) -> dict[str, Any]:
    """Load one local document, summarize it, and optionally create chunks."""

    decision = _document_read_decision(path, raw_requested_path)
    if not decision.allowed or not decision.resolved_path:
        return _document_access_failure(decision)
    loader = DocumentLoader()
    chunker = DocumentChunker()
    store = _store()
    loaded = loader.load_file(decision.resolved_path)
    if not loaded.get("success"):
        return _with_document_metadata(loaded, decision)
    data = loaded.get("data", {})
    if not isinstance(data, dict):
        return _with_document_metadata({"success": False, "error": "DocumentLoader returned incomplete data."}, decision)

    summary = loader.summarize_document(str(data.get("text", "")))
    record = build_record_from_loaded_document(data, summary)
    stored = store.add_document(record)
    if not stored.get("success"):
        return _with_document_metadata(
            {
                "success": False,
                "status": "failed",
                "error": stored.get("error") or "DocumentStore failed to store the document.",
                "data": {
                    "status": "failed",
                    "document_id": record.document_id,
                    "path": data.get("path", ""),
                    "source_path": data.get("path", ""),
                    "store_status": "failed",
                    "document_stored": False,
                    "chunks_stored": False,
                    "chunk_count": 0,
                },
            },
            decision,
        )
    stored_data = stored.get("data", {})
    stored_doc = stored_data.get("document", {}) if isinstance(stored_data, dict) else {}
    document_id = stored_doc.get("document_id", record.document_id) if isinstance(stored_doc, dict) else record.document_id

    chunks_created = False
    chunk_count = 0
    if create_chunks:
        chunks = chunker.chunk_text(
            text=str(data.get("text", "")),
            document_id=document_id,
            file_name=str(data.get("file_name", "")),
            path=str(data.get("path", "")),
            headings=summary.get("headings", []),
            chunk_size=chunk_size,
            overlap=overlap,
        )
        chunk_result = store.add_chunks(document_id, chunks)
        if not chunk_result.get("success"):
            return _with_document_metadata(
                {
                    "success": False,
                    "status": "partial",
                    "error": chunk_result.get("error") or "Document chunks could not be stored.",
                    "data": {
                        "status": "partial",
                        "document_id": document_id,
                        "path": data.get("path", ""),
                        "source_path": data.get("path", ""),
                        "store_status": "partial",
                        "document_stored": True,
                        "chunks_stored": False,
                        "chunk_count": 0,
                        "store_result": stored_data,
                    },
                },
                decision,
            )
        chunks_created = True
        chunk_count = len(chunks)

    text = str(data.get("text", ""))
    return _with_document_metadata({
        "success": True,
        "status": "success",
        "data": {
            "status": "success",
            "path": data.get("path", ""),
            "source_path": data.get("path", ""),
            "file_name": data.get("file_name", ""),
            "extension": data.get("extension", ""),
            "text_preview": text[:PREVIEW_CHARS],
            "summary": summary,
            "document_id": document_id,
            "truncated": data.get("truncated", False),
            "chunks_created": chunks_created,
            "document_stored": True,
            "chunks_stored": bool(not create_chunks or chunks_created),
            "chunk_count": chunk_count,
            "chunk_size": chunk_size,
            "overlap": overlap,
            "store_status": {
                "added": stored_data.get("added") if isinstance(stored_data, dict) else None,
                "updated": stored_data.get("updated") if isinstance(stored_data, dict) else None,
                "reason": stored_data.get("reason") if isinstance(stored_data, dict) else "",
            },
        },
    }, decision)


def load_documents_from_directory(
    path: str,
    recursive: bool = False,
    max_files: int = 20,
    create_chunks: bool = True,
    chunk_size: int = 800,
    overlap: int = 120,
    raw_requested_path: str | None = None,
) -> dict[str, Any]:
    """Batch-load supported documents from a local directory."""

    decision = _document_read_decision(path, raw_requested_path)
    if not decision.allowed or not decision.resolved_path:
        return _document_access_failure(decision)
    loader = DocumentLoader()
    chunker = DocumentChunker()
    store = _store()
    loaded = loader.load_directory(decision.resolved_path, recursive=recursive, max_files=max_files)
    if not loaded.get("success"):
        return _with_document_metadata(loaded, decision)

    data = loaded.get("data", {})
    if not isinstance(data, dict):
        return _with_document_metadata({"success": False, "error": "DocumentLoader returned incomplete data."}, decision)

    documents = []
    failures = []
    total_chunks_created = 0
    for item in data.get("documents", []):
        if not isinstance(item, dict):
            continue
        summary = loader.summarize_document(str(item.get("text", "")))
        record = build_record_from_loaded_document(item, summary)
        stored = store.add_document(record)
        if not stored.get("success"):
            failures.append(
                {
                    "path": item.get("path", ""),
                    "file_name": item.get("file_name", ""),
                    "document_id": record.document_id,
                    "status": "failed",
                    "document_stored": False,
                    "chunks_stored": False,
                    "chunk_count": 0,
                    "error": stored.get("error") or "DocumentStore failed to store the document.",
                }
            )
            continue
        stored_data = stored.get("data", {})
        stored_doc = stored_data.get("document", {}) if isinstance(stored_data, dict) else {}
        document_id = stored_doc.get("document_id", record.document_id) if isinstance(stored_doc, dict) else record.document_id
        chunk_count = 0
        chunks_stored = not create_chunks
        if create_chunks:
            chunks = chunker.chunk_text(
                text=str(item.get("text", "")),
                document_id=document_id,
                file_name=str(item.get("file_name", "")),
                path=str(item.get("path", "")),
                headings=summary.get("headings", []),
                chunk_size=chunk_size,
                overlap=overlap,
            )
            chunk_result = store.add_chunks(document_id, chunks)
            if chunk_result.get("success"):
                chunk_count = len(chunks)
                total_chunks_created += chunk_count
                chunks_stored = True
            else:
                failures.append(
                    {
                        "path": item.get("path", ""),
                        "file_name": item.get("file_name", ""),
                        "document_id": document_id,
                        "status": "partial",
                        "document_stored": True,
                        "chunks_stored": False,
                        "chunk_count": 0,
                        "error": chunk_result.get("error") or "Document chunks could not be stored.",
                    }
                )
                continue
        documents.append(
            {
                "path": item.get("path", ""),
                "source_path": item.get("path", ""),
                "file_name": item.get("file_name", ""),
                "extension": item.get("extension", ""),
                "document_id": document_id,
                "chunk_count": chunk_count,
                "status": "success",
                "document_stored": True,
                "chunks_stored": chunks_stored,
                "store_status": "success",
                "summary": summary.get("summary", ""),
                "headings": summary.get("headings", []),
                "truncated": item.get("truncated", False),
                "added": stored_data.get("added") if isinstance(stored_data, dict) else None,
                "updated": stored_data.get("updated") if isinstance(stored_data, dict) else None,
            }
        )

    skipped = data.get("skipped", [])
    skipped = skipped if isinstance(skipped, list) else []
    status = "partial" if failures else "success"
    return _with_document_metadata({
        "success": not failures,
        "status": status,
        "data": {
            "status": status,
            "loaded_count": len(documents),
            "failed_count": len(failures),
            "skipped_count": len(skipped),
            "total_chunks_created": total_chunks_created,
            "documents": documents,
            "failures": failures,
            "skipped": skipped,
        },
    }, decision)


def list_documents() -> dict[str, Any]:
    """List compact metadata for loaded documents."""

    return _store().list_documents()


def find_documents(keyword: str) -> dict[str, Any]:
    """Find loaded document records by keyword."""

    return _store().find_documents(keyword)


def remove_document(document_id_or_path: str) -> dict[str, Any]:
    """Remove one loaded document record."""

    return _store().remove_document(document_id_or_path)


def clear_documents() -> dict[str, Any]:
    """Clear all loaded document records."""

    return _store().clear_documents()


DOCUMENT_TOOLS = {
    "load_document": load_document,
    "load_documents_from_directory": load_documents_from_directory,
    "list_documents": list_documents,
    "find_documents": find_documents,
    "remove_document": remove_document,
    "clear_documents": clear_documents,
}


DOCUMENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "load_document",
            "description": "Import one local document into the workspace knowledge base. Persists DocumentStore metadata and creates chunks by default; changes internal state. Use read_document for temporary reading.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Local document path."},
                    "create_chunks": {"type": "boolean", "description": "Create chunks (default true).", "default": True},
                    "chunk_size": {"type": "integer", "description": "Chunk size (default 800).", "default": 800},
                    "overlap": {"type": "integer", "description": "Chunk overlap (default 120).", "default": 120},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_documents_from_directory",
            "description": "Import local documents from a directory into the workspace knowledge base. Persists DocumentStore metadata and creates chunks by default; changes internal state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Local directory path."},
                    "recursive": {"type": "boolean", "description": "Scan recursively.", "default": False},
                    "max_files": {"type": "integer", "description": "File limit (default 20).", "default": 20},
                    "create_chunks": {"type": "boolean", "description": "Create chunks (default true).", "default": True},
                    "chunk_size": {"type": "integer", "description": "Chunk size (default 800).", "default": 800},
                    "overlap": {"type": "integer", "description": "Chunk overlap (default 120).", "default": 120},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List loaded document metadata and summaries from DocumentStore.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_documents",
            "description": "Find loaded document metadata and summaries by keyword.",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "Search keyword."}},
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_document",
            "description": "Remove one document record by document_id or full path.",
            "parameters": {
                "type": "object",
                "properties": {"document_id_or_path": {"type": "string", "description": "document_id or full path."}},
                "required": ["document_id_or_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_documents",
            "description": "Clear all loaded document records.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
