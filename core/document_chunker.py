"""Rule-based document chunking for local documents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from core.document_store import utc_now


MIN_CHUNK_SIZE = 300
MAX_CHUNK_SIZE = 2000
DEFAULT_CHUNK_SIZE = 800
MIN_OVERLAP = 0
MAX_OVERLAP = 500
DEFAULT_OVERLAP = 120


@dataclass(frozen=True)
class HeadingPosition:
    """A markdown heading and its character offset."""

    offset: int
    heading: str


class DocumentChunker:
    """Split local document text into stable overlapping chunks."""

    def chunk_text(
        self,
        text: str,
        document_id: str,
        file_name: str,
        path: str,
        headings: list[str] | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> list[dict[str, Any]]:
        """Split text by character length with bounded overlap."""

        normalized_size, normalized_overlap = self._normalize_options(chunk_size, overlap)
        cleaned = self.clean_chunk_text(text or "")
        if not cleaned:
            return []

        heading_positions = _extract_heading_positions(cleaned)
        fallback_heading = headings[0] if headings else ""
        chunks: list[dict[str, Any]] = []
        start = 0
        index = 0
        created_at = utc_now()

        while start < len(cleaned):
            end = min(len(cleaned), start + normalized_size)
            chunk_text = self.clean_chunk_text(cleaned[start:end])
            if chunk_text.strip():
                heading = _nearest_heading(heading_positions, start) or fallback_heading
                content_hash = hashlib.sha256(chunk_text.encode("utf-8", errors="ignore")).hexdigest()
                chunk_id = f"{document_id}_{index:04d}_{content_hash[:12]}"
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "chunk_index": index,
                        "file_name": file_name,
                        "path": path,
                        "text": chunk_text,
                        "text_length": len(chunk_text),
                        "start_char": start,
                        "end_char": end,
                        "heading": heading,
                        "content_hash": content_hash,
                        "created_at": created_at,
                    }
                )
                index += 1
            if end >= len(cleaned):
                break
            start = max(end - normalized_overlap, start + 1)

        return chunks

    def clean_chunk_text(self, text: str) -> str:
        """Compact excessive blank lines while preserving indentation."""

        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\n{4,}", "\n\n\n", normalized).strip("\n")

    @staticmethod
    def _normalize_options(chunk_size: int, overlap: int) -> tuple[int, int]:
        try:
            size = int(chunk_size)
        except (TypeError, ValueError):
            size = DEFAULT_CHUNK_SIZE
        try:
            ov = int(overlap)
        except (TypeError, ValueError):
            ov = DEFAULT_OVERLAP
        size = max(MIN_CHUNK_SIZE, min(size, MAX_CHUNK_SIZE))
        ov = max(MIN_OVERLAP, min(ov, MAX_OVERLAP))
        ov = min(ov, size // 2)
        return size, ov


def _extract_heading_positions(text: str) -> list[HeadingPosition]:
    positions: list[HeadingPosition] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line.rstrip("\n"))
        if match:
            positions.append(HeadingPosition(offset=offset, heading=match.group(2).strip()))
        offset += len(line)
    return positions


def _nearest_heading(positions: list[HeadingPosition], offset: int) -> str:
    current = ""
    for item in positions:
        if item.offset > offset:
            break
        current = item.heading
    return current
