"""Tools for inspecting safe context status."""

from __future__ import annotations

from typing import Any

from core.document_store import DocumentStore
from core.persistent_memory import PersistentMemory
from core.vector_store import VectorStore
from core.workspace_runtime import get_current_workspace, get_document_dir, get_vector_dir
from tools.rag_tools import get_rag_status


def get_context_status() -> dict[str, Any]:
    """Return high-level context availability without contents."""

    workspace = get_current_workspace()
    memory = PersistentMemory(
        database_path=workspace.database_path,
        user_id=workspace.user_id,
        project_id=workspace.project_id,
    )
    memory_health = memory.load_all()
    document_store = DocumentStore(document_dir=get_document_dir())
    documents = document_store.list_documents().get("data", {})
    chunks = document_store.list_chunks(limit=1).get("data", {})
    vectors = VectorStore(vector_dir=get_vector_dir()).get_status().get("data", {})
    rag = get_rag_status().get("data", {})
    counts = memory.get_counts() if memory_health.get("success") else {}
    return {
        "success": True,
        "data": {
            "long_term_memory_available": any(value > 0 for value in counts.values()),
            "memory_counts": counts,
            "persistent_memory_healthy": memory_health.get("success") is True,
            "documents_available": bool(documents.get("documents_count", 0)) if isinstance(documents, dict) else False,
            "chunks_available": bool(chunks.get("chunks_count", 0)) if isinstance(chunks, dict) else False,
            "vectors_available": bool(vectors.get("vectors_count", 0)) if isinstance(vectors, dict) else False,
            "rag_ready": bool(rag.get("rag_ready")) if isinstance(rag, dict) else False,
            "fused_context_available": False,
        },
    }


def inspect_context_summary() -> dict[str, Any]:
    """Return a safe session-independent context summary."""

    context_status = get_context_status()
    status = context_status.get("data", {})
    return {
        "success": True,
        "data": {
            "current_task": "available inside AgentLoop TaskState",
            "memory_reference_guidance_available": bool(
                status.get("long_term_memory_available")
            ),
            "rag_context_note": "available after rag_query or chunk retrieval",
            "fused_context_note": "available after ContextFusionEngine runs",
            "notes": "This tool does not expose full memory, chunks, API keys, or .env content.",
        },
    }


CONTEXT_TOOLS = {
    "get_context_status": get_context_status,
    "inspect_context_summary": inspect_context_summary,
}


CONTEXT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_context_status",
            "description": "Check safe context availability: long-term memory, documents, chunks, vectors, RAG readiness, and fused context support.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_context_summary",
            "description": "Return a safe high-level context summary without full memory or chunk contents.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
