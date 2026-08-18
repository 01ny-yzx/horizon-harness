"""Deterministic model-visible compaction for parsed document results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import json
from typing import Any

from core.unicode_safety import sanitize_unicode


TEXT_KEYS = ("text", "content", "markdown", "body")
TABLE_KEYS = ("tables", "sheets")
MIN_VISIBLE_TABLES = 1
MIN_VISIBLE_TABLE_ROWS = 1
MIN_VISIBLE_TABLE_COLUMNS = 1
MAX_TABLE_CELL_CHARS = 80
MAX_TABLE_NAME_CHARS = 120


@dataclass(frozen=True)
class DocumentResultCompaction:
    compacted_result: Any
    was_compacted: bool
    omitted_fields: tuple[str, ...]
    full_result_required: bool
    original_serialized_chars: int
    visible_serialized_chars: int

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


def compact_document_result(
    result: Any,
    *,
    max_preview_chars: int,
    max_table_rows: int = 8,
    max_table_columns: int = 12,
    max_tables: int = 5,
) -> DocumentResultCompaction:
    """Compact every large document field under one deterministic budget."""

    value = sanitize_unicode(result)
    budget = max(200, int(max_preview_chars or 1200))
    original_chars = _serialized_chars(value)
    canonical = _canonicalize_result(
        value,
        max_table_rows=max_table_rows,
        max_table_columns=max_table_columns,
        max_tables=max_tables,
    )
    if _fits_without_compaction(
        canonical,
        budget=budget,
        max_table_rows=max_table_rows,
        max_table_columns=max_table_columns,
        max_tables=max_tables,
    ):
        visible_chars = _serialized_chars(canonical)
        return DocumentResultCompaction(canonical, False, (), False, original_chars, visible_chars)

    omitted: list[str] = []
    if isinstance(canonical, str):
        compacted: Any = {
            "excerpt": _truncate(canonical, max(80, budget - 120)),
            "truncated": True,
            "compacted": True,
        }
        omitted.append("text")
    elif isinstance(canonical, dict):
        compacted = _compact_mapping(
            canonical,
            budget=budget,
            max_table_rows=max_table_rows,
            max_table_columns=max_table_columns,
            max_tables=max_tables,
            omitted=omitted,
        )
    else:
        compacted = {
            "preview": _truncate(_canonical_json(canonical), max(80, budget - 120)),
            "truncated": True,
            "compacted": True,
        }
        omitted.append("result")

    normalized_omitted = tuple(_truncate(item, 80) for item in dict.fromkeys(omitted))[:50]
    if isinstance(compacted, dict):
        compacted["omitted_fields"] = list(normalized_omitted[:20])
        if len(normalized_omitted) > 20:
            compacted["omitted_field_count"] = len(normalized_omitted)
    compacted = _enforce_budget(compacted, budget)
    visible_chars = _serialized_chars(compacted)
    return DocumentResultCompaction(
        compacted_result=compacted,
        was_compacted=True,
        omitted_fields=normalized_omitted,
        full_result_required=True,
        original_serialized_chars=original_chars,
        visible_serialized_chars=visible_chars,
    )


def _canonicalize_result(
    value: Any,
    *,
    max_table_rows: int,
    max_table_columns: int,
    max_tables: int,
) -> Any:
    if not isinstance(value, dict):
        return value
    tables = _extract_tables(value)
    if not tables:
        return dict(value)
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    total_sheet_count = _count(value, metadata, "total_sheet_count", fallback=len(tables))
    parsed_sheet_count = _count(value, metadata, "parsed_sheet_count", fallback=len(tables))
    reader_omitted_sheets = _count(
        value,
        metadata,
        "reader_omitted_sheets",
        fallback=max(0, total_sheet_count - parsed_sheet_count),
    )
    canonical = {
        key: item
        for key, item in value.items()
        if key not in TABLE_KEYS and key != "metadata" and item not in (None, "", [], {})
    }
    canonical_tables = [
        _table_summary(table, max_rows=max_table_rows, max_columns=max_table_columns)
        for table in tables[:max_tables]
    ]
    visible_table_count = len(canonical_tables)
    canonical.update({
        "table_count": total_sheet_count,
        "total_sheet_count": total_sheet_count,
        "parsed_sheet_count": parsed_sheet_count,
        "visible_table_count": visible_table_count,
        "reader_omitted_tables": reader_omitted_sheets,
        "projection_omitted_tables": max(0, parsed_sheet_count - visible_table_count),
        "omitted_tables": max(0, total_sheet_count - visible_table_count),
        "tables": canonical_tables,
        "reader_truncated": bool(value.get("reader_truncated") or metadata.get("reader_truncated") or reader_omitted_sheets),
    })
    return sanitize_unicode(canonical)


def _fits_without_compaction(
    value: Any,
    *,
    budget: int,
    max_table_rows: int,
    max_table_columns: int,
    max_tables: int,
) -> bool:
    if _serialized_chars(value) > budget:
        return False
    if isinstance(value, dict):
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        if bool(value.get("reader_truncated") or metadata.get("reader_truncated")):
            return False
        if _count(value, metadata, "reader_omitted_sheets") > 0:
            return False
        if int(value.get("projection_omitted_tables") or 0) > 0 or int(value.get("omitted_tables") or 0) > 0:
            return False
    tables = _extract_tables(value)
    if len(tables) > max_tables:
        return False
    for table in tables:
        if bool(table.get("reader_truncated")):
            return False
        if int(table.get("reader_omitted_rows") or 0) > 0 or int(table.get("reader_omitted_columns") or 0) > 0:
            return False
        rows = _normalize_table_rows(table)
        column_count = max((len(row) for row in rows), default=0)
        if (
            int(table.get("projection_omitted_rows") or 0) > 0
            or int(table.get("projection_omitted_columns") or 0) > 0
            or len(rows) > max_table_rows
            or column_count > max_table_columns
        ):
            return False
    return True


def _compact_mapping(
    value: dict[str, Any],
    *,
    budget: int,
    max_table_rows: int,
    max_table_columns: int,
    max_tables: int,
    omitted: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    text = next((value.get(key) for key in TEXT_KEYS if isinstance(value.get(key), str) and value.get(key)), "")
    if text:
        excerpt_budget = min(max(120, budget // 3), 500)
        result["excerpt"] = _truncate(str(text), excerpt_budget)
        if len(str(text)) > excerpt_budget:
            omitted.append("text")

    tables = _extract_tables(value)
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    total_sheet_count = _count(value, metadata, "total_sheet_count", fallback=len(tables))
    parsed_sheet_count = _count(value, metadata, "parsed_sheet_count", fallback=len(tables))
    reader_omitted_sheets = _count(
        value,
        metadata,
        "reader_omitted_sheets",
        fallback=max(0, total_sheet_count - parsed_sheet_count),
    )
    table_summaries: list[dict[str, Any]] = []
    for table in tables[:max_tables]:
        summary = _table_summary(
            table,
            max_rows=max_table_rows,
            max_columns=max_table_columns,
        )
        candidate = {**result, "tables": [*table_summaries, summary]}
        if _serialized_chars(candidate) > budget and table_summaries:
            break
        table_summaries.append(summary)
    if tables:
        visible_table_count = len(table_summaries)
        result["table_count"] = total_sheet_count
        result["total_sheet_count"] = total_sheet_count
        result["parsed_sheet_count"] = parsed_sheet_count
        result["visible_table_count"] = visible_table_count
        result["reader_omitted_tables"] = reader_omitted_sheets
        result["projection_omitted_tables"] = max(0, parsed_sheet_count - visible_table_count)
        result["tables"] = table_summaries
        result["omitted_tables"] = max(0, total_sheet_count - visible_table_count)
        result["reader_truncated"] = bool(value.get("reader_truncated") or metadata.get("reader_truncated") or reader_omitted_sheets)
        if any(table.get("truncated") for table in table_summaries) or visible_table_count < total_sheet_count:
            omitted.append("tables")

    ignored = set(TEXT_KEYS) | set(TABLE_KEYS)
    for key in sorted(value):
        if key in ignored:
            continue
        item = value[key]
        if _is_small_metadata(item):
            candidate = {**result, key: item}
            if _serialized_chars(candidate) <= budget:
                result[key] = item
            else:
                omitted.append(key)
        else:
            omitted.append(key)

    result["omitted_fields"] = list(dict.fromkeys(omitted))
    result["truncated"] = True
    result["compacted"] = True
    return result


def _extract_tables(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    tables: list[dict[str, Any]] = []
    raw_tables = value.get("tables")
    if isinstance(raw_tables, list):
        tables.extend(item if isinstance(item, dict) else {"data": item} for item in raw_tables)
    elif isinstance(raw_tables, dict):
        for name in sorted(raw_tables):
            item = raw_tables[name]
            tables.append({**(item if isinstance(item, dict) else {"data": item}), "name": name})
    raw_sheets = value.get("sheets")
    if isinstance(raw_sheets, list):
        tables.extend(item if isinstance(item, dict) else {"data": item} for item in raw_sheets)
    elif isinstance(raw_sheets, dict):
        for name in sorted(raw_sheets):
            item = raw_sheets[name]
            tables.append({**(item if isinstance(item, dict) else {"data": item}), "name": name})
    return tables


def _table_summary(table: dict[str, Any], *, max_rows: int, max_columns: int) -> dict[str, Any]:
    rows = _normalize_table_rows(table)
    headers_value = table.get("headers") if isinstance(table.get("headers"), list) else table.get("columns")
    headers = list(headers_value) if isinstance(headers_value, (list, tuple)) else []
    observed_column_count = max([len(headers), *(len(row) for row in rows)] or [0])
    source_row_count = int(table.get("source_row_count") or len(rows))
    source_column_count = int(table.get("source_column_count") or observed_column_count)
    parsed_row_count = int(table.get("parsed_row_count") or len(rows))
    parsed_column_count = int(table.get("parsed_column_count") or observed_column_count)
    visible_headers = [_cell(value) for value in headers[:max_columns]]
    visible_rows = [[_cell(value) for value in row[:max_columns]] for row in rows[:max_rows]]
    name = _truncate_plain(str(table.get("sheet_name") or table.get("name") or table.get("title") or ""), MAX_TABLE_NAME_CHARS)
    visible_row_count = len(visible_rows)
    visible_column_count = max([len(visible_headers), *(len(row) for row in visible_rows)] or [0])
    reader_omitted_rows = max(0, source_row_count - parsed_row_count)
    projection_omitted_rows = max(0, parsed_row_count - visible_row_count)
    omitted_rows = max(0, source_row_count - visible_row_count)
    reader_omitted_columns = max(0, source_column_count - parsed_column_count)
    projection_omitted_columns = max(0, parsed_column_count - visible_column_count)
    omitted_columns = max(0, source_column_count - visible_column_count)
    return {
        "name": name,
        "source_row_count": source_row_count,
        "source_column_count": source_column_count,
        "parsed_row_count": parsed_row_count,
        "parsed_column_count": parsed_column_count,
        "visible_row_count": visible_row_count,
        "visible_column_count": visible_column_count,
        "row_count": source_row_count,
        "column_count": source_column_count,
        "headers": visible_headers,
        "preview_rows": visible_rows,
        "reader_omitted_rows": reader_omitted_rows,
        "reader_omitted_columns": reader_omitted_columns,
        "projection_omitted_rows": projection_omitted_rows,
        "projection_omitted_columns": projection_omitted_columns,
        "omitted_rows": omitted_rows,
        "omitted_columns": omitted_columns,
        "reader_truncated": bool(table.get("reader_truncated") or reader_omitted_rows or reader_omitted_columns),
        "truncated": bool(table.get("reader_truncated") or omitted_rows or omitted_columns),
    }


def _normalize_table_rows(table: Mapping[str, Any]) -> list[list[Any]]:
    raw_rows = next(
        (table.get(key) for key in ("preview_rows", "rows", "data", "values") if isinstance(table.get(key), (list, tuple))),
        [],
    )
    rows: list[list[Any]] = []
    for item in raw_rows:
        if isinstance(item, (list, tuple)):
            rows.append(list(item))
        else:
            rows.append([item])
    return rows


def _is_small_metadata(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 240
    if isinstance(value, list):
        return len(value) <= 12 and _serialized_chars(value) <= 400
    if isinstance(value, dict):
        return len(value) <= 12 and _serialized_chars(value) <= 400
    return False


def _enforce_budget(value: dict[str, Any], budget: int) -> dict[str, Any]:
    result = sanitize_unicode(dict(value))
    source_has_tables = bool(result.get("tables"))

    # Text and descriptive metadata yield before structured table evidence.
    if _serialized_chars(result) > budget and isinstance(result.get("excerpt"), str):
        result["excerpt"] = _truncate_plain(result["excerpt"], min(80, max(20, budget // 8)))
    if _serialized_chars(result) > budget:
        result.pop("excerpt", None)
    for key in ("omitted_fields", "omitted_field_count"):
        if _serialized_chars(result) > budget:
            result.pop(key, None)

    core_top = {
        "table_count", "total_sheet_count", "parsed_sheet_count", "visible_table_count", "tables",
        "reader_omitted_tables", "projection_omitted_tables", "omitted_tables", "reader_truncated",
        "truncated", "compacted",
    }
    for key in sorted(set(result) - core_top):
        if _serialized_chars(result) <= budget:
            break
        result.pop(key, None)

    tables = [dict(table) for table in result.get("tables") or [] if isinstance(table, dict)]
    while _serialized_chars({**result, "tables": tables}) > budget and len(tables) > MIN_VISIBLE_TABLES:
        tables.pop()
    result["tables"] = tables
    _refresh_table_list_counts(result)

    # Keep at least one row in every retained table before reducing columns.
    while _serialized_chars(result) > budget:
        changed = False
        for table in reversed(tables):
            rows = [list(row) for row in table.get("preview_rows") or [] if isinstance(row, list)]
            if len(rows) > MIN_VISIBLE_TABLE_ROWS:
                rows.pop()
                table["preview_rows"] = rows
                _refresh_table_projection_counts(table)
                changed = True
                break
        if not changed:
            break

    while _serialized_chars(result) > budget:
        changed = False
        for table in reversed(tables):
            rows = [list(row) for row in table.get("preview_rows") or [] if isinstance(row, list)]
            headers = list(table.get("headers") or []) if isinstance(table.get("headers"), list) else []
            width = max([len(headers), *(len(row) for row in rows)] or [0])
            if width > MIN_VISIBLE_TABLE_COLUMNS:
                table["preview_rows"] = [row[: width - 1] for row in rows]
                if headers:
                    table["headers"] = headers[: width - 1]
                _refresh_table_projection_counts(table)
                changed = True
                break
        if not changed:
            break

    for text_limit in (40, 20, 8, 1):
        if _serialized_chars(result) <= budget:
            break
        for table in tables:
            table["name"] = _truncate_plain(str(table.get("name") or ""), min(MAX_TABLE_NAME_CHARS, text_limit))
            table["preview_rows"] = [
                [_truncate_plain(str(cell), text_limit) if isinstance(cell, str) else cell for cell in row]
                for row in table.get("preview_rows") or []
                if isinstance(row, list)
            ]
            if isinstance(table.get("headers"), list):
                table["headers"] = [
                    _truncate_plain(str(cell), text_limit) if isinstance(cell, str) else cell
                    for cell in table["headers"]
                ]
            _refresh_table_projection_counts(table)

    if source_has_tables and tables and _serialized_chars(result) > budget:
        # Extreme fallback: preserve one cell plus all count semantics instead of erasing table identity.
        first = tables[0]
        rows = [list(row) for row in first.get("preview_rows") or [] if isinstance(row, list)]
        first_row = rows[0][:1] if rows and rows[0] else []
        minimal_table = {
            key: first.get(key)
            for key in (
                "name", "source_row_count", "source_column_count", "parsed_row_count", "parsed_column_count",
                "visible_row_count", "visible_column_count", "row_count", "column_count", "reader_omitted_rows",
                "reader_omitted_columns", "projection_omitted_rows", "projection_omitted_columns", "omitted_rows",
                "omitted_columns", "reader_truncated", "truncated",
            )
        }
        minimal_table["preview_rows"] = [first_row] if first_row else []
        _refresh_table_projection_counts(minimal_table)
        result = {
            key: result.get(key)
            for key in (
                "table_count", "total_sheet_count", "parsed_sheet_count", "reader_omitted_tables",
                "projection_omitted_tables", "omitted_tables", "reader_truncated", "truncated", "compacted",
            )
        }
        result["tables"] = [minimal_table]
        _refresh_table_list_counts(result)
    return sanitize_unicode(result)


def _refresh_table_projection_counts(table: dict[str, Any]) -> None:
    rows = table.get("preview_rows") if isinstance(table.get("preview_rows"), list) else []
    headers = table.get("headers") if isinstance(table.get("headers"), list) else []
    visible_rows = [row for row in rows if isinstance(row, list)]
    visible_row_count = len(visible_rows)
    visible_column_count = max([len(headers), *(len(row) for row in visible_rows)] or [0])
    source_rows = int(table.get("source_row_count") or table.get("row_count") or 0)
    source_columns = int(table.get("source_column_count") or table.get("column_count") or 0)
    parsed_rows = int(table.get("parsed_row_count") or 0)
    parsed_columns = int(table.get("parsed_column_count") or 0)
    table["visible_row_count"] = visible_row_count
    table["visible_column_count"] = visible_column_count
    table["projection_omitted_rows"] = max(0, parsed_rows - visible_row_count)
    table["projection_omitted_columns"] = max(0, parsed_columns - visible_column_count)
    table["omitted_rows"] = max(0, source_rows - visible_row_count)
    table["omitted_columns"] = max(0, source_columns - visible_column_count)
    table["truncated"] = bool(table["omitted_rows"] or table["omitted_columns"])


def _refresh_table_list_counts(result: dict[str, Any]) -> None:
    visible = len(result.get("tables") or [])
    total = int(result.get("total_sheet_count") or result.get("table_count") or 0)
    parsed = int(result.get("parsed_sheet_count") or visible)
    result["visible_table_count"] = visible
    result["projection_omitted_tables"] = max(0, parsed - visible)
    result["omitted_tables"] = max(0, total - visible)


def _count(primary: dict[str, Any], metadata: dict[str, Any], key: str, *, fallback: int = 0) -> int:
    for source in (primary, metadata):
        value = source.get(key)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    return max(0, int(fallback))


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate_plain(str(value), MAX_TABLE_CELL_CHARS)


def _truncate_plain(value: str, limit: int) -> str:
    return str(value or "")[: max(0, int(limit))]


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _canonical_json(value: Any) -> str:
    return json.dumps(sanitize_unicode(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _serialized_chars(value: Any) -> int:
    return len(_canonical_json(value))


__all__ = ["DocumentResultCompaction", "compact_document_result"]
