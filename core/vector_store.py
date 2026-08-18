"""Local JSON vector store for chunk embeddings."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTOR_DIR = PROJECT_ROOT / "vector_store"
VECTORS_FILE = "vectors.json"


class VectorStore:
    """Persist chunk embeddings in local JSON."""

    def __init__(self, vector_dir: str | Path | None = None) -> None:
        self.store_dir = Path(vector_dir) if vector_dir is not None else DEFAULT_VECTOR_DIR
        self.vectors_path = self.store_dir / VECTORS_FILE
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def load(self) -> list[dict[str, Any]]:
        """Load vector records."""

        self._ensure_file()
        try:
            payload = json.loads(self.vectors_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"vectors": []}
        vectors = payload.get("vectors", []) if isinstance(payload, dict) else []
        return [record for record in vectors if isinstance(record, dict)]

    def save(self, vectors: list[dict[str, Any]]) -> None:
        """Save vector records."""

        model = ""
        for record in vectors:
            if record.get("embedding_model"):
                model = str(record.get("embedding_model"))
                break
        self.vectors_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "embedding_model": model,
                    "vectors_count": len(vectors),
                    "last_updated": utc_now(),
                    "vectors": vectors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def upsert_vector(self, record: dict[str, Any]) -> dict[str, Any]:
        """Insert or update one vector by chunk_id."""

        chunk_id = str(record.get("chunk_id", ""))
        if not chunk_id:
            return {"success": False, "error": "chunk_id cannot be empty."}
        vectors = self.load()
        now = utc_now()
        item = dict(record)
        item.setdefault("created_at", now)
        item["updated_at"] = now
        existing_index = next((index for index, value in enumerate(vectors) if value.get("chunk_id") == chunk_id), None)
        if existing_index is None:
            vectors.append(item)
            changed = "created"
        else:
            existing = vectors[existing_index]
            item["created_at"] = existing.get("created_at", now)
            vectors[existing_index] = item
            changed = "updated"
        self.save(vectors)
        return {"success": True, "data": {"status": changed, "vector": _metadata_only(item), "vectors_count": len(vectors)}}

    def upsert_vectors(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Insert or update many vector records."""

        created = updated = failed = 0
        for record in records:
            before = self.get_vector(str(record.get("chunk_id", ""))).get("success")
            result = self.upsert_vector(record)
            if not result.get("success"):
                failed += 1
            elif before:
                updated += 1
            else:
                created += 1
        return {"success": True, "data": {"created": created, "updated": updated, "failed": failed}}

    def get_vector(self, chunk_id: str) -> dict[str, Any]:
        """Return one vector record including embedding."""

        for record in self.load():
            if record.get("chunk_id") == chunk_id:
                return {"success": True, "data": record}
        return {"success": False, "error": f"Vector not found: {chunk_id}"}

    def remove_vector(self, chunk_id: str) -> dict[str, Any]:
        """Remove one vector by chunk id."""

        vectors = self.load()
        kept = [record for record in vectors if record.get("chunk_id") != chunk_id]
        deleted = len(vectors) - len(kept)
        self.save(kept)
        return {"success": True, "data": {"deleted": deleted, "vectors_count": len(kept)}}

    def remove_vectors_for_document(self, document_id: str) -> dict[str, Any]:
        """Remove all vectors for one document."""

        vectors = self.load()
        kept = [record for record in vectors if record.get("document_id") != document_id]
        deleted = len(vectors) - len(kept)
        self.save(kept)
        return {"success": True, "data": {"document_id": document_id, "deleted": deleted, "vectors_count": len(kept)}}

    def clear_vectors(self) -> dict[str, Any]:
        """Clear all vectors."""

        self.save([])
        return {"success": True, "data": {"deleted": "all", "vectors_count": 0}}

    def list_vectors(self, limit: int = 50) -> dict[str, Any]:
        """List vector metadata without embedding arrays."""

        safe_limit = _safe_limit(limit, default=50, maximum=500)
        vectors = self.load()
        return {
            "success": True,
            "data": {
                "vectors_count": len(vectors),
                "vectors": [_metadata_only(record) for record in vectors[:safe_limit]],
            },
        }

    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.2,
    ) -> dict[str, Any]:
        """Search by cosine similarity."""

        safe_top_k = _safe_limit(top_k, default=5, maximum=50)
        matches = []
        skipped = 0
        for record in self.load():
            embedding = record.get("embedding", [])
            if not isinstance(embedding, list) or len(embedding) != len(query_embedding):
                skipped += 1
                continue
            score = cosine_similarity(query_embedding, [float(value) for value in embedding])
            if score < threshold:
                continue
            item = _metadata_only(record)
            item["score"] = score
            matches.append(item)
        matches.sort(key=lambda item: -float(item.get("score", 0.0)))
        return {
            "success": True,
            "data": {
                "count": len(matches[:safe_top_k]),
                "total_matches": len(matches),
                "skipped": skipped,
                "matches": matches[:safe_top_k],
            },
        }

    def get_status(self) -> dict[str, Any]:
        """Return vector store status."""

        vectors = self.load()
        dims: dict[int, int] = {}
        models: dict[str, int] = {}
        for record in vectors:
            dim = int(record.get("embedding_dim", 0) or 0)
            dims[dim] = dims.get(dim, 0) + 1
            model = str(record.get("embedding_model", ""))
            models[model] = models.get(model, 0) + 1
        return {"success": True, "data": {"vectors_count": len(vectors), "embedding_dims": dims, "embedding_models": models}}

    def _ensure_file(self) -> None:
        if not self.vectors_path.exists():
            self.vectors_path.write_text(
                json.dumps(
                    {"version": 1, "embedding_model": "", "vectors_count": 0, "last_updated": "", "vectors": []},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Return cosine similarity for equal-length vectors."""

    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec1, vec2, strict=True))
    norm1 = math.sqrt(sum(float(a) * float(a) for a in vec1))
    norm2 = math.sqrt(sum(float(b) * float(b) for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def utc_now() -> str:
    """Return an ISO UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _metadata_only(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": record.get("chunk_id", ""),
        "document_id": record.get("document_id", ""),
        "file_name": record.get("file_name", ""),
        "path": record.get("path", ""),
        "heading": record.get("heading", ""),
        "embedding_dim": record.get("embedding_dim", 0),
        "embedding_model": record.get("embedding_model", ""),
        "content_hash": record.get("content_hash", ""),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
    }


def _safe_limit(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))
