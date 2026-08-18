"""Shared Database MCP query result presentation helpers."""

from __future__ import annotations

from typing import Any


DEFAULT_MAX_CELL_CHARS = 500
DEFAULT_PREVIEW_ROWS = 5


def truncate_cell_value(value: Any, *, max_cell_chars: int = DEFAULT_MAX_CELL_CHARS) -> tuple[Any, bool]:
    if value is None or isinstance(value, (int, float, bool)):
        return value, False
    if isinstance(value, bytes):
        return f"<binary {len(value)} bytes>", True
    if isinstance(value, bytearray):
        return f"<binary {len(value)} bytes>", True
    text = str(value)
    if len(text) <= max_cell_chars:
        return text, False
    return f"{text[:max_cell_chars]}... [truncated]", True


def format_query_result(
    *,
    driver: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    max_rows: int,
    limited: bool = True,
    query_type: str = "select",
    max_cell_chars: int = DEFAULT_MAX_CELL_CHARS,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    returned_source_rows = rows[: max_rows + 1]
    rows_truncated = len(returned_source_rows) > max_rows
    visible_rows = returned_source_rows[:max_rows]
    formatted_rows, cells_truncated = _format_rows(visible_rows, max_cell_chars=max_cell_chars)
    returned_rows = len(formatted_rows)
    truncated = rows_truncated or cells_truncated
    summary = summarize_rows(returned_rows=returned_rows, rows_truncated=rows_truncated, cells_truncated=cells_truncated)
    payload = {
        "success": True,
        "driver": driver,
        "query_type": query_type,
        "columns": columns,
        "rows": formatted_rows,
        "row_count": returned_rows,
        "returned_rows": returned_rows,
        "max_rows": max_rows,
        "limited": bool(limited or rows_truncated),
        "truncated": truncated,
        "truncation": {
            "rows_truncated": rows_truncated,
            "cells_truncated": cells_truncated,
            "max_cell_chars": max_cell_chars,
        },
        "summary": summary,
        "preview": {"columns": columns, "rows": formatted_rows[:DEFAULT_PREVIEW_ROWS]},
    }
    payload.update(extra or {})
    return payload


def format_sample_rows(
    *,
    driver: str,
    table: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    max_rows: int,
    limit: int,
    max_cell_chars: int = DEFAULT_MAX_CELL_CHARS,
) -> dict[str, Any]:
    payload = format_query_result(
        driver=driver,
        columns=columns,
        rows=rows,
        max_rows=max_rows,
        limited=True,
        query_type="sample",
        max_cell_chars=max_cell_chars,
        extra={"table": table, "limit": limit},
    )
    payload["summary"] = f"Showing {payload['returned_rows']} sample rows from {table}."
    return payload


def format_table_schema(*, driver: str, table: str, columns: list[dict[str, Any]]) -> dict[str, Any]:
    preview = [_format_column(column) for column in columns]
    primary_keys = [column["name"] for column in preview if column["primary_key"]]
    column_count = len(columns)
    if primary_keys:
        pk_text = ", ".join(primary_keys)
        summary = f"Table {table} has {column_count} columns. Primary key: {pk_text}."
    else:
        summary = f"Table {table} has {column_count} columns. No primary key was reported."
    return {
        "success": True,
        "driver": driver,
        "table": table,
        "columns": columns,
        "count": column_count,
        "column_count": column_count,
        "primary_keys": primary_keys,
        "schema_summary": summary,
        "columns_preview": preview,
    }


def format_list_tables(*, driver: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    table_count = sum(1 for table in tables if str(table.get("type", "")).lower() == "table")
    view_count = sum(1 for table in tables if str(table.get("type", "")).lower() == "view")
    count = len(tables)
    return {
        "success": True,
        "driver": driver,
        "tables": tables,
        "count": count,
        "table_count": table_count,
        "view_count": view_count,
        "summary": f"Found {count} database objects: {table_count} tables and {view_count} views.",
    }


def format_sql_rejection(error_code: str, message: str, sql: str, *, read_only: bool = True) -> dict[str, Any]:
    blocked_operation = infer_blocked_operation(sql)
    return {
        "success": False,
        "error_code": error_code,
        "code": error_code,
        "error": message,
        "message": message,
        "blocked_operation": blocked_operation,
        "suggestion": "Use a SELECT query to inspect data. Write operations are not supported.",
        "read_only": read_only,
    }


def summarize_rows(*, returned_rows: int, rows_truncated: bool, cells_truncated: bool) -> str:
    if returned_rows == 0:
        return "Returned 0 rows."
    if rows_truncated and cells_truncated:
        return f"Returned {returned_rows} rows. Results and long cell values were truncated to protect performance."
    if rows_truncated:
        return f"Returned {returned_rows} rows. Results were truncated to protect performance."
    if cells_truncated:
        return f"Returned {returned_rows} rows. Long cell values were truncated."
    return f"Returned {returned_rows} rows."


def infer_blocked_operation(sql: str) -> str:
    text = str(sql or "").strip()
    normalized = text.replace("\n", " ")
    for phrase in ("INTO OUTFILE", "INTO DUMPFILE", "FOR UPDATE", "LOCK IN SHARE MODE"):
        if _contains_phrase(normalized, phrase):
            return phrase
    for operation in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "REPLACE",
        "MERGE",
        "ATTACH",
        "DETACH",
        "VACUUM",
        "REINDEX",
        "ANALYZE",
        "PRAGMA",
        "LOAD_EXTENSION",
        "CALL",
        "EXEC",
        "GRANT",
        "REVOKE",
        "LOCK",
        "UNLOCK",
        "SET",
        "USE",
    ):
        if _contains_word(normalized, operation):
            return operation
    if ";" in normalized:
        return "MULTI_STATEMENT"
    return "UNKNOWN"


def _format_rows(rows: list[dict[str, Any]], *, max_cell_chars: int) -> tuple[list[dict[str, Any]], bool]:
    cells_truncated = False
    formatted_rows: list[dict[str, Any]] = []
    for row in rows:
        formatted_row: dict[str, Any] = {}
        for key, value in row.items():
            formatted_value, truncated = truncate_cell_value(value, max_cell_chars=max_cell_chars)
            formatted_row[key] = formatted_value
            cells_truncated = cells_truncated or truncated
        formatted_rows.append(formatted_row)
    return formatted_rows, cells_truncated


def _format_column(column: dict[str, Any]) -> dict[str, Any]:
    name = str(column.get("name", ""))
    column_type = str(column.get("type", ""))
    nullable = _column_nullable(column)
    default = column.get("default")
    primary_key = bool(column.get("primary_key", column.get("pk", False)))
    parts = [name, column_type]
    if primary_key:
        parts.append("primary key")
    parts.append("nullable" if nullable else "not null")
    if default is not None:
        parts.append(f"default {default}")
    return {
        "name": name,
        "type": column_type,
        "nullable": nullable,
        "default": default,
        "primary_key": primary_key,
        "description": " ".join(part for part in parts if part),
    }


def _column_nullable(column: dict[str, Any]) -> bool:
    if "nullable" in column:
        return bool(column["nullable"])
    if "notnull" in column:
        return not bool(column["notnull"])
    return True


def _contains_word(text: str, word: str) -> bool:
    import re

    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


def _contains_phrase(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()
