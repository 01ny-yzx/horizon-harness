"""JSON-backed metadata store for loaded local documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_DIR = PROJECT_ROOT / "document_store"
INDEX_FILE = "documents_index.json"
LOADED_FILE = "loaded_documents.json"
CHUNKS_FILE = "chunks.json"
MAX_DOCUMENT_RECORDS = 100
MAX_SUMMARY_CHARS = 2000
MAX_CHUNK_PREVIEW_CHARS = 240


@dataclass
class DocumentRecord:
    """Metadata and compact summary for one loaded document."""

    document_id: str
    path: str
    file_name: str
    extension: str
    size_bytes: int
    text_length: int
    summary: str
    headings: list[str]
    loaded_at: str
    updated_at: str
    source: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DocumentStore:
    """Persist loaded document metadata without storing full original text."""

    def __init__(self, document_dir: str | Path | None = None) -> None:
        self.store_dir = Path(document_dir) if document_dir is not None else DEFAULT_STORE_DIR
        self.index_path = self.store_dir / INDEX_FILE
        self.loaded_path = self.store_dir / LOADED_FILE
        self.chunks_path = self.store_dir / CHUNKS_FILE
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def add_document(self, record: dict[str, Any] | DocumentRecord) -> dict[str, Any]:
        """Add or update a document record by path/content hash."""

        records = self._load_records()
        item = record.to_dict() if isinstance(record, DocumentRecord) else dict(record)
        item["summary"] = str(item.get("summary", ""))[:MAX_SUMMARY_CHARS]
        item["headings"] = list(item.get("headings", []))[:20]
        now = utc_now()

        existing_index = self._find_existing_index(records, item)
        if existing_index is not None:
            existing = records[existing_index]
            if existing.get("content_hash") == item.get("content_hash"):
                if existing.get("path") != item.get("path"):
                    existing["path"] = item.get("path", existing.get("path", ""))
                    existing["updated_at"] = now
                    records[existing_index] = existing
                    self._save_records(records)
                return {
                    "success": True,
                    "data": {
                        "document": existing,
                        "added": False,
                        "updated": False,
                        "reason": "content_hash unchanged",
                    },
                }
            item["document_id"] = existing.get("document_id") or item.get("document_id")
            item["loaded_at"] = existing.get("loaded_at") or item.get("loaded_at") or now
            item["updated_at"] = now
            records[existing_index] = item
            changed = "updated"
        else:
            item.setdefault("document_id", self.make_document_id(item.get("path", ""), item.get("content_hash", "")))
            item.setdefault("loaded_at", now)
            item.setdefault("updated_at", now)
            item.setdefault("source", "document_loader")
            records.append(item)
            changed = "added"

        records = records[-MAX_DOCUMENT_RECORDS:]
        self._save_records(records)
        return {
            "success": True,
            "data": {
                "document": item,
                "added": changed == "added",
                "updated": changed == "updated",
                "documents_count": len(records),
            },
        }

    def list_documents(self) -> dict[str, Any]:
        """List all stored document records."""

        records = self._load_records()
        return {"success": True, "data": {"documents_count": len(records), "documents": records}}

    def get_document(self, document_id: str) -> dict[str, Any]:
        """Return one record by document id."""

        for record in self._load_records():
            if record.get("document_id") == document_id:
                return {"success": True, "data": record}
        return {"success": False, "error": f"未找到文档记录：{document_id}"}

    def find_documents(self, keyword: str) -> dict[str, Any]:
        """Search records by keyword across metadata and summaries."""

        needle = (keyword or "").strip().lower()
        if not needle:
            return {"success": False, "error": "keyword 不能为空。"}
        matches = []
        for record in self._load_records():
            haystack = json.dumps(record, ensure_ascii=False).lower()
            if needle in haystack:
                matches.append(record)
        return {"success": True, "data": {"keyword": keyword, "count": len(matches), "documents": matches}}

    def remove_document(self, document_id_or_path: str) -> dict[str, Any]:
        """Remove records matching an id or path."""

        target = (document_id_or_path or "").strip()
        if not target:
            return {"success": False, "error": "document_id_or_path 不能为空。"}
        records = self._load_records()
        deleted_ids = [
            str(record.get("document_id", ""))
            for record in records
            if record.get("document_id") == target or str(record.get("path", "")) == target
        ]
        kept = [
            record
            for record in records
            if record.get("document_id") != target and str(record.get("path", "")) != target
        ]
        deleted = len(records) - len(kept)
        self._save_records(kept)
        for document_id in deleted_ids:
            if document_id:
                self.remove_chunks_for_document(document_id)
        return {"success": True, "data": {"deleted": deleted, "documents_count": len(kept)}}

    def clear_documents(self) -> dict[str, Any]:
        """Clear all document records."""

        self._save_records([])
        self.clear_chunks()
        return {"success": True, "data": {"deleted": "all", "documents_count": 0}}

    def add_chunks(self, document_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace chunks for one document id."""

        if not document_id:
            return {"success": False, "error": "document_id cannot be empty."}
        existing = [chunk for chunk in self._load_chunks() if chunk.get("document_id") != document_id]
        clean_chunks = [dict(chunk) for chunk in chunks if isinstance(chunk, dict) and chunk.get("chunk_id")]
        all_chunks = existing + clean_chunks
        self._save_chunks(all_chunks)
        return {
            "success": True,
            "data": {
                "document_id": document_id,
                "chunk_count": len(clean_chunks),
                "chunks_count": len(all_chunks),
            },
        }

    def list_chunks(self, document_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        """List chunk metadata with short previews."""

        safe_limit = _safe_limit(limit, default=50, maximum=200)
        chunks = self._load_chunks()
        if document_id:
            chunks = [chunk for chunk in chunks if chunk.get("document_id") == document_id]
        items = [_chunk_preview(chunk) for chunk in chunks[:safe_limit]]
        return {
            "success": True,
            "data": {
                "document_id": document_id or "",
                "count": len(items),
                "chunks_count": len(chunks),
                "chunks": items,
            },
        }

    def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        """Return one chunk with full text."""

        if not chunk_id:
            return {"success": False, "error": "chunk_id cannot be empty."}
        for chunk in self._load_chunks():
            if chunk.get("chunk_id") == chunk_id:
                return {"success": True, "data": chunk}
        return {"success": False, "error": f"Chunk not found: {chunk_id}"}

    def find_chunks(
        self,
        keyword: str,
        document_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Search chunk text, heading, and file name with simple keyword scoring."""

        needle = (keyword or "").strip().lower()
        if not needle:
            return {"success": False, "error": "keyword cannot be empty."}
        safe_limit = _safe_limit(limit, default=5, maximum=20)
        matches = []
        for chunk in self._load_chunks():
            if document_id and chunk.get("document_id") != document_id:
                continue
            text = str(chunk.get("text", ""))
            heading = str(chunk.get("heading", ""))
            file_name = str(chunk.get("file_name", ""))
            score = text.lower().count(needle)
            if needle in heading.lower():
                score += 3
            if needle in file_name.lower():
                score += 2
            if score <= 0:
                continue
            item = dict(chunk)
            item["score"] = score
            matches.append(item)
        matches.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("file_name", "")), int(item.get("chunk_index", 0))))
        return {
            "success": True,
            "data": {
                "keyword": keyword,
                "document_id": document_id or "",
                "count": len(matches[:safe_limit]),
                "total_matches": len(matches),
                "chunks": matches[:safe_limit],
            },
        }

    def remove_chunks_for_document(self, document_id: str) -> dict[str, Any]:
        """Remove all chunks for one document."""

        if not document_id:
            return {"success": False, "error": "document_id cannot be empty."}
        chunks = self._load_chunks()
        kept = [chunk for chunk in chunks if chunk.get("document_id") != document_id]
        deleted = len(chunks) - len(kept)
        self._save_chunks(kept)
        _remove_vectors_for_document(document_id)
        return {"success": True, "data": {"document_id": document_id, "deleted": deleted, "chunks_count": len(kept)}}

    def clear_chunks(self) -> dict[str, Any]:
        """Clear all stored chunks."""

        self._save_chunks([])
        _clear_vectors()
        return {"success": True, "data": {"deleted": "all", "chunks_count": 0}}

    def format_chunks_for_prompt(self, chunks: list[dict[str, Any]], max_chars: int = 3000) -> str:
        """Format selected chunks for prompt injection."""

        if not chunks:
            return "ChunkStore: no matching chunks."
        lines = [f"ChunkStore: {len(chunks)} retrieved chunk(s)."]
        for chunk in chunks:
            excerpt = " ".join(str(chunk.get("text", "")).split())
            lines.append(
                "\n".join(
                    [
                        f"- chunk_id: {chunk.get('chunk_id', '')}",
                        f"  file_name: {chunk.get('file_name', '')}",
                        f"  heading: {chunk.get('heading', '') or 'none'}",
                        f"  excerpt: {excerpt[:700]}",
                    ]
                )
            )
            if len("\n".join(lines)) >= max_chars:
                break
        return "\n".join(lines)[:max_chars]

    def format_for_prompt(self, max_chars: int = 2000) -> str:
        """Return a compact index suitable for context injection."""

        records = self._load_records()
        if not records:
            return "DocumentStore: no loaded documents."
        lines = [f"DocumentStore: {len(records)} loaded document(s)."]
        for record in records[-20:]:
            headings = ", ".join(record.get("headings", [])[:5])
            summary = " ".join(str(record.get("summary", "")).split())[:240]
            lines.append(
                "- "
                f"id={record.get('document_id')} "
                f"file={record.get('file_name')} "
                f"type={record.get('extension')} "
                f"chars={record.get('text_length')} "
                f"headings={headings or 'none'} "
                f"summary={summary}"
            )
        text = "\n".join(lines)
        return text[:max_chars]

    def _ensure_files(self) -> None:
        if not self.index_path.exists():
            self.index_path.write_text(
                json.dumps(
                    {"version": 1, "documents_count": 0, "last_updated": "", "documents": []},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        if not self.loaded_path.exists():
            self.loaded_path.write_text(
                json.dumps({"documents": []}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if not self.chunks_path.exists():
            self.chunks_path.write_text(
                json.dumps({"version": 1, "chunks_count": 0, "last_updated": "", "chunks": []}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _load_records(self) -> list[dict[str, Any]]:
        self._ensure_files()
        try:
            payload = json.loads(self.loaded_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"documents": []}
        documents = payload.get("documents", []) if isinstance(payload, dict) else []
        return [record for record in documents if isinstance(record, dict)]

    def _save_records(self, records: list[dict[str, Any]]) -> None:
        now = utc_now()
        records = records[-MAX_DOCUMENT_RECORDS:]
        self.loaded_path.write_text(
            json.dumps({"documents": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.index_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "documents_count": len(records),
                    "last_updated": now,
                    "documents": records,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load_chunks(self) -> list[dict[str, Any]]:
        self._ensure_files()
        try:
            payload = json.loads(self.chunks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"chunks": []}
        chunks = payload.get("chunks", []) if isinstance(payload, dict) else []
        return [chunk for chunk in chunks if isinstance(chunk, dict)]

    def _save_chunks(self, chunks: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.chunks_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "chunks_count": len(chunks),
                    "last_updated": now,
                    "chunks": chunks,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _find_existing_index(records: list[dict[str, Any]], item: dict[str, Any]) -> int | None:
        item_path = str(item.get("path", ""))
        item_id = str(item.get("document_id", ""))
        item_hash = str(item.get("content_hash", ""))
        item_name = str(item.get("file_name", ""))
        for index, record in enumerate(records):
            if item_id and record.get("document_id") == item_id:
                return index
            if item_path and record.get("path") == item_path:
                return index
            if (
                item_hash
                and item_name
                and record.get("content_hash") == item_hash
                and record.get("file_name") == item_name
            ):
                return index
        return None

    @staticmethod
    def make_document_id(path: str, content_hash: str) -> str:
        seed = f"{path}|{content_hash}".encode("utf-8", errors="ignore")
        return hashlib.sha256(seed).hexdigest()[:16]


def build_record_from_loaded_document(loaded: dict[str, Any], summary: dict[str, Any]) -> DocumentRecord:
    """Create a DocumentRecord from DocumentLoader output."""

    text = str(loaded.get("text", ""))
    content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    now = utc_now()
    return DocumentRecord(
        document_id=DocumentStore.make_document_id(str(loaded.get("path", "")), content_hash),
        path=str(loaded.get("path", "")),
        file_name=str(loaded.get("file_name", "")),
        extension=str(loaded.get("extension", "")),
        size_bytes=int(loaded.get("size_bytes", 0) or 0),
        text_length=int(loaded.get("text_length", 0) or 0),
        summary=str(summary.get("summary", ""))[:MAX_SUMMARY_CHARS],
        headings=[str(item) for item in summary.get("headings", [])[:20]],
        loaded_at=now,
        updated_at=now,
        source="document_loader",
        content_hash=content_hash,
    )


def utc_now() -> str:
    """Return an ISO UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _chunk_preview(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "document_id": chunk.get("document_id", ""),
        "chunk_index": chunk.get("chunk_index", 0),
        "file_name": chunk.get("file_name", ""),
        "path": chunk.get("path", ""),
        "text_preview": str(chunk.get("text", ""))[:MAX_CHUNK_PREVIEW_CHARS],
        "text_length": chunk.get("text_length", 0),
        "start_char": chunk.get("start_char", 0),
        "end_char": chunk.get("end_char", 0),
        "heading": chunk.get("heading", ""),
        "created_at": chunk.get("created_at", ""),
    }


def _safe_limit(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _remove_vectors_for_document(document_id: str) -> None:
    try:
        from core.vector_store import VectorStore
        from core.workspace_runtime import get_vector_dir

        VectorStore(vector_dir=get_vector_dir()).remove_vectors_for_document(document_id)
    except Exception:
        return


def _clear_vectors() -> None:
    try:
        from core.vector_store import VectorStore
        from core.workspace_runtime import get_vector_dir

        VectorStore(vector_dir=get_vector_dir()).clear_vectors()
    except Exception:
        return
