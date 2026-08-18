"""Unified local document reader for file and document tools."""

from __future__ import annotations

import csv
import importlib
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_CHARS = 12000
DEFAULT_MAX_TABLE_ROWS = 50
DEFAULT_MAX_SHEETS = 5
DEFAULT_MAX_SHEET_COLS = 20

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx"}
LEGACY_EXTENSIONS = {".doc": ".docx", ".xls": ".xlsx"}
@dataclass
class DocumentReadResult:
    success: bool
    path: str
    file_type: str
    text: str = ""
    tables: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    size_bytes: int | None = None
    page_count: int | None = None
    sheet_count: int | None = None
    total_sheet_count: int | None = None
    parsed_sheet_count: int | None = None
    reader_omitted_sheets: int = 0
    reader_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class XlsxSheetReadResult:
    rows: list[list[str]]
    source_row_count: int
    source_column_count: int
    parsed_row_count: int
    parsed_column_count: int
    reader_omitted_rows: int
    reader_omitted_columns: int
    reader_truncated: bool


def read_document(
    path: str | Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_tables: bool = True,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_table_rows: int = DEFAULT_MAX_TABLE_ROWS,
    max_table_cols: int = DEFAULT_MAX_SHEET_COLS,
    max_sheets: int = DEFAULT_MAX_SHEETS,
) -> DocumentReadResult:
    """Read a bounded local document without network, LLM, or MCP calls."""

    requested = str(path)
    file_type = _file_type_from_suffix(Path(requested).suffix.lower())
    try:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            return _failure(requested, file_type, "document_file_not_found", f"文件不存在：{requested}")
        if target.is_dir():
            return _failure(str(target), file_type, "document_path_is_directory", f"这是目录，不是文件：{requested}")
        suffix = target.suffix.lower()
        file_type = _file_type_from_suffix(suffix)
        if suffix in LEGACY_EXTENSIONS:
            return _failure(
                str(target),
                file_type,
                "document_legacy_format_unsupported",
                f"暂不支持老旧 {suffix} 格式，请转换为 {LEGACY_EXTENSIONS[suffix]} 后再读取。",
            )
        if suffix not in SUPPORTED_EXTENSIONS:
            return _failure(str(target), file_type, "document_unsupported_type", f"暂不支持该文件格式：{suffix or '(无扩展名)'}")

        size = target.stat().st_size
        if size > max_file_bytes:
            return _failure(
                str(target),
                file_type,
                "document_file_too_large",
                f"文件过大：{size} bytes，当前最大支持 {max_file_bytes} bytes。",
                size_bytes=size,
            )

        if suffix in {".txt", ".md", ".markdown"}:
            result = _read_text_document(target, file_type=file_type)
        elif suffix == ".json":
            result = _read_json_document(target)
        elif suffix == ".csv":
            result = _read_csv_document(target, include_tables=include_tables, max_table_rows=max_table_rows, max_table_cols=max_table_cols)
        elif suffix == ".pdf":
            result = _read_pdf_document(target)
        elif suffix == ".docx":
            result = _read_docx_document(target, include_tables=include_tables, max_table_rows=max_table_rows, max_table_cols=max_table_cols)
        elif suffix == ".xlsx":
            result = _read_xlsx_document(
                target,
                include_tables=include_tables,
                max_sheets=max_sheets,
                max_table_rows=max_table_rows,
                max_table_cols=max_table_cols,
            )
        else:
            result = _failure(str(target), file_type, "document_unsupported_type", f"暂不支持该文件格式：{suffix}")

        result.size_bytes = size
        result.path = str(target)
        return _limit_result_text(result, max_chars=max_chars)
    except PermissionError:
        return _failure(requested, file_type, "document_read_failed", "没有权限读取该文件。")
    except Exception as exc:  # noqa: BLE001
        return _failure(requested, file_type, "document_read_failed", f"读取文档失败：{type(exc).__name__}")


def _read_text_document(target: Path, *, file_type: str) -> DocumentReadResult:
    warnings: list[str] = []
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = target.read_text(encoding="utf-8", errors="replace")
        warnings.append("文件包含非 UTF-8 或损坏字符，已使用替换字符安全读取。")
    return DocumentReadResult(
        success=True,
        path=str(target),
        file_type=file_type,
        text=_compact_blank_lines(text),
        metadata={"chars_read": len(text)},
        warnings=warnings,
    )


def _read_json_document(target: Path) -> DocumentReadResult:
    raw = target.read_text(encoding="utf-8", errors="replace")
    warnings: list[str] = []
    try:
        parsed = json.loads(raw)
        text = json.dumps(parsed, ensure_ascii=False, indent=2)
        metadata = {"valid_json": True, "chars_read": len(text)}
    except json.JSONDecodeError:
        text = raw
        warnings.append("JSON 解析失败，已按普通文本读取。")
        metadata = {"valid_json": False, "chars_read": len(text)}
    return DocumentReadResult(True, str(target), "json", _compact_blank_lines(text), metadata=metadata, warnings=warnings)


def _read_csv_document(target: Path, *, include_tables: bool, max_table_rows: int, max_table_cols: int) -> DocumentReadResult:
    raw = target.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    rows: list[list[str]] = []
    truncated = False
    for row_index, row in enumerate(reader):
        if row_index >= max_table_rows:
            truncated = True
            break
        rows.append([cell.strip() for cell in row[:max_table_cols]])
    columns = rows[0] if rows else []
    text = _rows_to_text("CSV", rows)
    return DocumentReadResult(
        True,
        str(target),
        "csv",
        text,
        tables=[{"name": "CSV", "columns": columns, "rows": rows[1:]}] if include_tables and rows else [],
        metadata={"rows_read": max(0, len(rows) - 1), "columns": columns, "truncated": truncated},
        truncated=truncated,
    )


def _read_pdf_document(target: Path) -> DocumentReadResult:
    reader_class = _optional_pdf_reader()
    if reader_class is None:
        return _failure(str(target), "pdf", "document_pdf_dependency_missing", "缺少 PDF 文本提取依赖，无法读取 PDF。")
    try:
        reader = reader_class(str(target))
        pages = list(getattr(reader, "pages", []))
        parts = []
        for page in pages:
            extract_text = getattr(page, "extract_text", None)
            if callable(extract_text):
                parts.append(extract_text() or "")
        text = _compact_blank_lines("\n\n".join(parts))
        warnings = [] if text else ["PDF 没有提取到正文；如果是扫描版 PDF，需要 OCR，本工具当前不做 OCR。"]
        return DocumentReadResult(True, str(target), "pdf", text, metadata={"page_count": len(pages), "chars_read": len(text)}, warnings=warnings, page_count=len(pages))
    except Exception:
        return _failure(str(target), "pdf", "document_read_failed", "PDF 读取失败，可能是文件损坏或加密。")


def _read_docx_document(target: Path, *, include_tables: bool, max_table_rows: int, max_table_cols: int) -> DocumentReadResult:
    try:
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml")
    except KeyError:
        return _failure(str(target), "word", "document_docx_invalid_or_corrupt", "DOCX 文件结构不完整。")
    except zipfile.BadZipFile:
        return _failure(
            str(target),
            "word",
            "document_docx_invalid_or_corrupt",
            "DOCX 文件损坏或不是有效的 DOCX。",
        )
    except ElementTree.ParseError:
        return _failure(
            str(target),
            "word",
            "document_docx_invalid_or_corrupt",
            "DOCX 核心文档结构损坏。",
        )

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return _failure(
            str(target),
            "word",
            "document_docx_invalid_or_corrupt",
            "DOCX 核心文档结构损坏。",
        )
    paragraphs = [_node_text(node) for node in root.findall(".//w:p", _ns())]
    paragraphs = [text for text in paragraphs if text.strip()]
    tables = _extract_docx_tables(root, max_table_rows=max_table_rows, max_table_cols=max_table_cols) if include_tables else []
    text_parts = paragraphs[:]
    for index, table in enumerate(tables, start=1):
        text_parts.append(_rows_to_text(f"DOCX Table {index}", table.get("rows", [])))
    text = _compact_blank_lines("\n\n".join(part for part in text_parts if part))
    return DocumentReadResult(
        True,
        str(target),
        "word",
        text,
        tables=tables,
        metadata={"paragraphs": len(paragraphs), "tables": len(tables), "chars_read": len(text), "truncated": any(t.get("truncated") for t in tables)},
        truncated=any(t.get("truncated") for t in tables),
    )


def _read_xlsx_document(target: Path, *, include_tables: bool, max_sheets: int, max_table_rows: int, max_table_cols: int) -> DocumentReadResult:
    try:
        with zipfile.ZipFile(target) as archive:
            shared_strings = _read_xlsx_shared_strings(archive)
            sheet_names = _read_xlsx_sheet_names(archive)
            sheet_paths = sorted(
                (name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name)),
                key=lambda name: int(re.search(r"sheet(\d+)\.xml$", name).group(1)),
            )
            tables = []
            text_parts = []
            sheet_results: list[XlsxSheetReadResult] = []
            for index, sheet_path in enumerate(sheet_paths[:max_sheets]):
                sheet_name = sheet_names[index] if index < len(sheet_names) else Path(sheet_path).stem
                sheet_result = _read_xlsx_sheet(
                    archive.read(sheet_path),
                    shared_strings,
                    max_rows=max_table_rows,
                    max_cols=max_table_cols,
                )
                sheet_results.append(sheet_result)
                table = {
                    "name": sheet_name,
                    "rows": sheet_result.rows,
                    "source_row_count": sheet_result.source_row_count,
                    "source_column_count": sheet_result.source_column_count,
                    "parsed_row_count": sheet_result.parsed_row_count,
                    "parsed_column_count": sheet_result.parsed_column_count,
                    "reader_omitted_rows": sheet_result.reader_omitted_rows,
                    "reader_omitted_columns": sheet_result.reader_omitted_columns,
                    "reader_truncated": sheet_result.reader_truncated,
                    "truncated": sheet_result.reader_truncated,
                }
                if include_tables:
                    tables.append(table)
                text_parts.append(_rows_to_text(f"Sheet: {sheet_name}", sheet_result.rows))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return _failure(
            str(target),
            "excel",
            "document_xlsx_invalid_or_corrupt",
            "XLSX 文件损坏或不是有效的 XLSX。",
        )

    total_sheet_count = len(sheet_paths)
    parsed_sheet_count = min(total_sheet_count, max_sheets)
    reader_omitted_sheets = max(0, total_sheet_count - parsed_sheet_count)
    reader_truncated = reader_omitted_sheets > 0 or any(result.reader_truncated for result in sheet_results)
    text = _compact_blank_lines("\n\n".join(part for part in text_parts if part))
    return DocumentReadResult(
        True,
        str(target),
        "excel",
        text,
        tables=tables,
        metadata={
            "sheet_count": total_sheet_count,
            "total_sheet_count": total_sheet_count,
            "parsed_sheet_count": parsed_sheet_count,
            "reader_omitted_sheets": reader_omitted_sheets,
            "reader_truncated": reader_truncated,
            "parsed_sheet_names": sheet_names[:parsed_sheet_count],
            "sheets": sheet_names[:parsed_sheet_count],
            "source_rows_total": sum(result.source_row_count for result in sheet_results),
            "parsed_rows_total": sum(result.parsed_row_count for result in sheet_results),
            "reader_omitted_rows_total": sum(result.reader_omitted_rows for result in sheet_results),
            "rows_read": sum(result.parsed_row_count for result in sheet_results),
            "truncated": reader_truncated,
        },
        truncated=reader_truncated,
        sheet_count=total_sheet_count,
        total_sheet_count=total_sheet_count,
        parsed_sheet_count=parsed_sheet_count,
        reader_omitted_sheets=reader_omitted_sheets,
        reader_truncated=reader_truncated,
    )


def _limit_result_text(result: DocumentReadResult, *, max_chars: int) -> DocumentReadResult:
    safe_max = max(1, int(max_chars or DEFAULT_MAX_CHARS))
    if len(result.text) > safe_max:
        result.text = result.text[:safe_max].rstrip()
        result.truncated = True
        result.metadata["truncated"] = True
        result.warnings.append(f"输出已按 max_chars={safe_max} 截断。")
    result.metadata.setdefault("chars_read", len(result.text))
    return result


def _failure(path: str, file_type: str, code: str, message: str, *, size_bytes: int | None = None) -> DocumentReadResult:
    return DocumentReadResult(
        success=False,
        path=str(path),
        file_type=file_type,
        error={"code": code, "message": message},
        size_bytes=size_bytes,
    )


def _file_type_from_suffix(suffix: str) -> str:
    return {
        ".txt": "text",
        ".md": "markdown",
        ".markdown": "markdown",
        ".json": "json",
        ".csv": "csv",
        ".pdf": "pdf",
        ".docx": "word",
        ".xlsx": "excel",
        ".doc": "unsupported_legacy_word",
        ".xls": "unsupported_legacy_excel",
    }.get(suffix.lower(), "unsupported")


def _optional_pdf_reader() -> Any:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        reader = getattr(module, "PdfReader", None)
        if reader is not None:
            return reader
    return None


def _compact_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def _rows_to_text(name: str, rows: list[list[Any]]) -> str:
    if not rows:
        return name
    lines = [name]
    lines.extend(" | ".join(str(cell) for cell in row) for row in rows)
    return "\n".join(lines)


def _ns() -> dict[str, str]:
    return {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    }


def _node_text(node: ElementTree.Element) -> str:
    return "".join(text_node.text or "" for text_node in node.findall(".//w:t", _ns())).strip()


def _extract_docx_tables(root: ElementTree.Element, *, max_table_rows: int, max_table_cols: int) -> list[dict[str, Any]]:
    tables = []
    for table_index, table_node in enumerate(root.findall(".//w:tbl", _ns()), start=1):
        rows = []
        truncated = False
        for row_index, row_node in enumerate(table_node.findall(".//w:tr", _ns())):
            if row_index >= max_table_rows:
                truncated = True
                break
            rows.append([_node_text(cell) for cell in row_node.findall(".//w:tc", _ns())[:max_table_cols]])
        tables.append({"name": f"Table {table_index}", "rows": rows, "truncated": truncated})
    return tables


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [_node_spreadsheet_text(item) for item in root.findall(".//a:si", _ns())]


def _read_xlsx_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    except KeyError:
        return []
    return [str(sheet.attrib.get("name", "")) for sheet in root.findall(".//a:sheet", _ns()) if sheet.attrib.get("name")]


_XLSX_CELL_REF_RE = re.compile(r"^([A-Za-z]+)([0-9]+)$")


def _xlsx_row_index(row_node: ElementTree.Element, fallback_index: int) -> int:
    value = str(row_node.attrib.get("r") or "")
    return int(value) if value.isdigit() and int(value) > 0 else fallback_index


def _xlsx_column_index(cell_ref: Any, fallback_index: int) -> int:
    match = _XLSX_CELL_REF_RE.fullmatch(str(cell_ref or ""))
    if not match:
        return fallback_index
    column = 0
    for character in match.group(1).upper():
        column = column * 26 + ord(character) - ord("A") + 1
    return column


def _read_xlsx_sheet(xml: bytes, shared_strings: list[str], *, max_rows: int, max_cols: int) -> XlsxSheetReadResult:
    root = ElementTree.fromstring(xml)
    rows: list[list[str]] = []
    source_row_count = 0
    source_column_count = 0
    parsed_column_count = 0
    for row_position, row_node in enumerate(root.findall(".//a:row", _ns()), start=1):
        source_row_count = max(source_row_count, _xlsx_row_index(row_node, row_position))
        materialize_row = len(rows) < max_rows
        visible_cells: dict[int, str] = {}
        for cell_position, cell in enumerate(row_node.findall("a:c", _ns()), start=1):
            column_index = _xlsx_column_index(cell.attrib.get("r"), cell_position)
            source_column_count = max(source_column_count, column_index)
            if materialize_row and column_index <= max_cols:
                visible_cells[column_index] = _read_xlsx_cell(cell, shared_strings)
                parsed_column_count = max(parsed_column_count, column_index)
        if materialize_row:
            width = min(max_cols, max(visible_cells, default=0))
            rows.append([visible_cells.get(column, "") for column in range(1, width + 1)])
    parsed_row_count = len(rows)
    reader_omitted_rows = max(0, source_row_count - parsed_row_count)
    reader_omitted_columns = max(0, source_column_count - parsed_column_count)
    return XlsxSheetReadResult(
        rows=rows,
        source_row_count=source_row_count,
        source_column_count=source_column_count,
        parsed_row_count=parsed_row_count,
        parsed_column_count=parsed_column_count,
        reader_omitted_rows=reader_omitted_rows,
        reader_omitted_columns=reader_omitted_columns,
        reader_truncated=bool(reader_omitted_rows or reader_omitted_columns),
    )


def _read_xlsx_cell(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _node_spreadsheet_text(cell)
    value = cell.find("a:v", _ns())
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s" and raw.isdigit():
        index = int(raw)
        return shared_strings[index] if 0 <= index < len(shared_strings) else raw
    return raw


def _node_spreadsheet_text(node: ElementTree.Element) -> str:
    return "".join(text_node.text or "" for text_node in node.findall(".//a:t", _ns())).strip()
