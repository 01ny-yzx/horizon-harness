"""Parse authorized host document paths and build bounded text projections."""

from __future__ import annotations

import csv
import io
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from core.document_readers import DEFAULT_MAX_FILE_BYTES, SUPPORTED_EXTENSIONS, read_document

MAX_FILE_BYTES = DEFAULT_MAX_FILE_BYTES
LOADER_TEXT_EXTENSIONS = {".py", ".html", ".htm"}
LOADER_SUPPORTED_EXTENSIONS = set(SUPPORTED_EXTENSIONS) | LOADER_TEXT_EXTENSIONS
class DocumentLoader:
    """Parse paths already resolved by a tool access boundary."""

    def __init__(self, max_file_bytes: int = MAX_FILE_BYTES) -> None:
        self.max_file_bytes = max_file_bytes

    def load_file(self, path: str) -> dict[str, Any]:
        """Load one supported local file and return structured data."""

        try:
            target = Path(path).expanduser().resolve()
            safety_error = self._validate_file_path(target, original=path)
            if safety_error:
                return {"success": False, "error": safety_error}

            extension = target.suffix.lower()
            if extension in SUPPORTED_EXTENSIONS:
                read_result = read_document(target, max_file_bytes=self.max_file_bytes)
                if not read_result.success:
                    message = read_result.error.get("message", "读取文档失败") if read_result.error else "读取文档失败"
                    return {"success": False, "error": message, "data": read_result.to_dict()}
                text = self.clean_text(read_result.text, extension=extension)
                metadata = read_result.metadata
                truncated = read_result.truncated
            else:
                raw_text = self._read_utf8_text(target)
                text = self.clean_text(raw_text, extension=extension)
                metadata = {}
                truncated = False
            return {
                "success": True,
                "data": {
                    "path": str(target),
                    "file_name": target.name,
                    "extension": extension,
                    "size_bytes": target.stat().st_size,
                    "text": text,
                    "text_length": len(text),
                    "truncated": truncated,
                    "metadata": metadata,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"读取文档失败：{exc}"}

    def load_directory(
        self,
        path: str,
        recursive: bool = False,
        max_files: int = 20,
    ) -> dict[str, Any]:
        """Scan and load supported files from a directory."""

        try:
            target = Path(path).expanduser().resolve()
            if not target.exists():
                return {"success": False, "error": f"目录不存在：{path}"}
            if not target.is_dir():
                return {"success": False, "error": f"不是目录：{path}"}
            safe_max = max(1, min(int(max_files or 20), 100))
            files = self._iter_supported_files(target, recursive=recursive)
            documents: list[dict[str, Any]] = []
            skipped: list[dict[str, str]] = []

            for candidate in files:
                if len(documents) >= safe_max:
                    skipped.append({"path": str(candidate), "reason": "达到 max_files 限制"})
                    continue
                result = self.load_file(str(candidate))
                if not result.get("success"):
                    skipped.append({"path": str(candidate), "reason": str(result.get("error", ""))})
                    continue
                data = result.get("data", {})
                if isinstance(data, dict):
                    documents.append(
                        {
                            "path": data.get("path", ""),
                            "file_name": data.get("file_name", ""),
                            "extension": data.get("extension", ""),
                            "size_bytes": data.get("size_bytes", 0),
                            "text_length": data.get("text_length", 0),
                            "truncated": data.get("truncated", False),
                            "text": data.get("text", ""),
                        }
                    )

            return {
                "success": True,
                "data": {
                    "path": str(target),
                    "recursive": bool(recursive),
                    "max_files": safe_max,
                    "loaded_count": len(documents),
                    "skipped_count": len(skipped),
                    "documents": documents,
                    "skipped": skipped[:20],
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"加载目录文档失败：{exc}"}

    def clean_text(self, text: str, extension: str = "") -> str:
        """Clean text while preserving code structure when needed."""

        extension = (extension or "").lower()
        if extension in {".html", ".htm"}:
            text = _extract_html_text(text)
            return _compact_blank_lines(text)
        if extension == ".json":
            return _format_json_text(text)
        if extension == ".csv":
            return _format_csv_text(text)
        if extension == ".py":
            return _compact_blank_lines(text.rstrip())
        return _normalize_prose_text(text)

    def summarize_document(self, text: str, max_chars: int = 1200) -> dict[str, Any]:
        """Create a simple rule-based summary without calling an LLM."""

        cleaned = _compact_blank_lines(text or "")
        headings = _extract_headings(cleaned)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
        title = _guess_title(cleaned, headings)
        body_parts = []
        for paragraph in paragraphs:
            if title and paragraph.strip() == title:
                continue
            body_parts.append(paragraph)
            if sum(len(part) for part in body_parts) >= max_chars:
                break
        summary = "\n\n".join(body_parts).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip() + "\n... [summary truncated]"
        if not summary:
            summary = cleaned[:max_chars].strip()
        return {
            "title": title,
            "summary": summary,
            "char_count": len(cleaned),
            "headings": headings[:20],
        }

    def get_supported_extensions(self) -> list[str]:
        """Return supported file extensions."""

        return sorted(LOADER_SUPPORTED_EXTENSIONS)

    def _validate_file_path(self, target: Path, original: str) -> str:
        if not target.exists():
            return f"文件不存在：{original}"
        if not target.is_file():
            return f"不是文件：{original}"
        extension = target.suffix.lower()
        if extension not in LOADER_SUPPORTED_EXTENSIONS:
            return f"当前 Document Loader v2 暂不支持该格式：{extension or '(无扩展名)'}"
        size = target.stat().st_size
        if size > self.max_file_bytes:
            return f"文件过大：{size} bytes，当前最大支持 {self.max_file_bytes} bytes"
        if extension not in SUPPORTED_EXTENSIONS and _looks_binary(target):
            return "当前 Document Loader v2 只读取受支持的本地文档，不读取其他二进制文件。"
        return ""

    def _read_utf8_text(self, target: Path) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return target.read_text(encoding="utf-8", errors="ignore")

    def _iter_supported_files(self, target: Path, recursive: bool) -> list[Path]:
        pattern = "**/*" if recursive else "*"
        files = []
        for candidate in sorted(target.glob(pattern)):
            if not candidate.is_file():
                continue
            files.append(candidate)
        return files


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag.lower() in {"p", "div", "br", "li", "section", "article", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "li", "section", "article", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _extract_html_text(text: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(text or "")
    return " ".join("".join(parser.parts).split())


def _format_json_text(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _normalize_prose_text(text)
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _format_csv_text(text: str, max_rows: int = 50, max_cols: int = 12) -> str:
    reader = csv.reader(io.StringIO(text))
    rows = []
    for row_index, row in enumerate(reader):
        if row_index >= max_rows:
            rows.append(["..."])
            break
        rows.append([cell.strip() for cell in row[:max_cols]])
    if not rows:
        return ""
    return "\n".join(" | ".join(row) for row in rows)


def _normalize_prose_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        lines.append(re.sub(r"[ \t]+", " ", line).strip())
    return _compact_blank_lines("\n".join(lines))


def _compact_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def _extract_headings(text: str) -> list[str]:
    headings = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            headings.append(stripped.lstrip("#").strip())
        elif len(stripped) <= 80 and re.match(r"^[A-Z0-9\u4e00-\u9fff][^。.!?]{2,}$", stripped):
            headings.append(stripped)
    return list(dict.fromkeys(headings))


def _guess_title(text: str, headings: list[str]) -> str:
    if headings:
        return headings[0]
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return ""


def _looks_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\x00" in sample
