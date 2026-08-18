"""Read-only Database MCP server over newline-delimited JSON-RPC stdio."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcp_servers.database_result_ux import (
    format_list_tables,
    format_query_result,
    format_sample_rows,
    format_sql_rejection,
    format_table_schema,
)

MAX_TEXT_CHARS = 12000
DEFAULT_QUERY_LIMIT = 100
MAX_QUERY_LIMIT = 100
DEFAULT_SAMPLE_LIMIT = 10
MAX_SAMPLE_LIMIT = 50
DEFAULT_MYSQL_PORT = 3306
DEFAULT_MYSQL_CHARSET = "utf8mb4"
DEFAULT_MYSQL_CONNECT_TIMEOUT = 5
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_PATTERN = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
DANGEROUS_SQL_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|merge|attach|detach|vacuum|reindex|analyze|pragma|load_extension|call|exec|grant|revoke|lock|unlock|set|use)\b",
    re.IGNORECASE,
)
READ_ONLY_SQL_PATTERN = re.compile(r"^(select|with|explain|show|describe|desc)\b", re.IGNORECASE)
LIMIT_PATTERN = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
SENSITIVE_PATH_PARTS = {".env", ".git", "node_modules", ".venv", "venv"}
SECRET_FIELD_MARKERS = ("password", "token", "secret", "api_key", "apikey", "credential")
DATABASE_WRITE_TOOL_NAMES = {
    "database_execute_write",
    "database_update_rows",
    "database_delete_rows",
    "database_drop_table",
    "database_write_rows",
}
DATABASE_WRITE_NAME_MARKERS = ("insert", "update", "delete", "drop", "alter", "create", "replace", "truncate", "merge", "write", "execute")


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_database_status",
        "description": "Check read-only Database MCP configuration status.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "permission_level": "read_only",
    },
    {
        "name": "database_test_connection",
        "description": "Test the configured read-only database connection.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "permission_level": "read_only",
    },
    {
        "name": "database_get_connection_config",
        "description": "Return a safe database connection configuration preview and diagnostics.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "permission_level": "read_only",
    },
    {
        "name": "database_list_tables",
        "description": "List tables and optionally views from the configured read-only database.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_views": {"type": "boolean", "default": True}},
            "required": [],
        },
        "permission_level": "read_only",
    },
    {
        "name": "database_describe_table",
        "description": "Describe columns for one database table using a validated identifier.",
        "inputSchema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "Table name matching ^[A-Za-z_][A-Za-z0-9_]*$."}},
            "required": ["table"],
        },
        "permission_level": "read_only",
    },
    {
        "name": "database_select_query",
        "description": "Execute one safe read-only SELECT query with a bounded row limit.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Single SELECT statement only."},
                "limit": {"type": "integer", "default": DEFAULT_QUERY_LIMIT, "maximum": MAX_QUERY_LIMIT},
            },
            "required": ["sql"],
        },
        "permission_level": "read_only",
    },
    {
        "name": "database_get_sample_rows",
        "description": "Read a small sample from one database table using a validated identifier.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name matching ^[A-Za-z_][A-Za-z0-9_]*$."},
                "limit": {"type": "integer", "default": DEFAULT_SAMPLE_LIMIT, "maximum": MAX_SAMPLE_LIMIT},
            },
            "required": ["table"],
        },
        "permission_level": "read_only",
    },
]


class DatabaseBackend(Protocol):
    driver: str

    def status(self) -> dict[str, Any]: ...
    def connection_config(self) -> dict[str, Any]: ...
    def test_connection(self) -> dict[str, Any]: ...
    def list_tables(self, include_views: bool = True) -> dict[str, Any]: ...
    def describe_table(self, table: str) -> dict[str, Any]: ...
    def select_query(self, sql: str, limit: int | None = None) -> dict[str, Any]: ...
    def sample_rows(self, table: str, limit: int | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DatabaseConfigIssue:
    code: str
    field: str
    severity: str
    message: str
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field": self.field,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class DatabaseConnectionConfig:
    driver: str
    read_only: bool
    config_preview: dict[str, Any]


@dataclass(frozen=True)
class DatabaseConnectionDiagnostic:
    driver: str
    configured: bool
    read_only: bool
    dependency_available: bool
    issues: list[DatabaseConfigIssue]
    config_preview: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        issues = [issue.to_dict() for issue in self.issues]
        return {
            "success": not any(issue.severity == "error" for issue in self.issues),
            "driver": self.driver,
            "configured": self.configured,
            "read_only": self.read_only,
            "dependency_available": self.dependency_available,
            "issues": issues,
            "config_preview": self.config_preview,
            "next_steps": _next_steps(issues),
        }


class UnsupportedDatabaseBackend:
    driver = "unsupported"

    def __init__(self, requested_driver: str) -> None:
        self.requested_driver = requested_driver

    def status(self) -> dict[str, Any]:
        return self.connection_config()

    def connection_config(self) -> dict[str, Any]:
        issue = _issue(
            "database_driver_unsupported",
            "DATABASE_MCP_DRIVER",
            "Unsupported Database MCP driver.",
            "Set DATABASE_MCP_DRIVER to sqlite or mysql.",
        )
        diagnostic = DatabaseConnectionDiagnostic(
            driver=self.requested_driver,
            configured=False,
            read_only=True,
            dependency_available=False,
            issues=[issue],
            config_preview={"driver": self.requested_driver, "read_only": True},
        ).to_dict()
        return {**diagnostic, "error_code": issue.code, "error": issue.message, "message": issue.message}

    def test_connection(self) -> dict[str, Any]:
        return self.status()

    def list_tables(self, include_views: bool = True) -> dict[str, Any]:
        return self.status()

    def describe_table(self, table: str) -> dict[str, Any]:
        return self.status()

    def select_query(self, sql: str, limit: int | None = None) -> dict[str, Any]:
        return self.status()

    def sample_rows(self, table: str, limit: int | None = None) -> dict[str, Any]:
        return self.status()


class SQLiteDatabaseBackend:
    """Small read-only SQLite backend for Database MCP v1."""

    driver = "sqlite"

    def __init__(self, path: str | None = None) -> None:
        self.path = path if path is not None else os.environ.get("DATABASE_MCP_SQLITE_PATH", "")

    def status(self) -> dict[str, Any]:
        return self.connection_config()

    def connection_config(self) -> dict[str, Any]:
        diagnostic = self._diagnostic().to_dict()
        issue = diagnostic["issues"][0] if diagnostic["issues"] else None
        diagnostic["database_path_preview"] = diagnostic["config_preview"].get("path_preview", "")
        diagnostic["error"] = "" if issue is None else issue["message"]
        diagnostic["error_code"] = "" if issue is None else issue["code"]
        return diagnostic

    def test_connection(self) -> dict[str, Any]:
        diagnostic = self._diagnostic()
        if diagnostic.issues:
            payload = diagnostic.to_dict()
            issue = payload["issues"][0]
            return {**payload, "connected": False, "error_code": issue["code"], "error": issue["message"], "message": issue["message"]}
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return {"success": True, "connected": True, "driver": self.driver, "read_only": True}
        except Exception as exc:  # noqa: BLE001 - normalize DB failures.
            diagnostic_payload = self._diagnostic().to_dict()
            return _failure(
                "database_sqlite_connection_failed",
                "SQLite connection failed.",
                {
                    "connected": False,
                    "driver": self.driver,
                    "suggestion": "Please check the SQLite path and read-only file permissions.",
                    "detail": _safe_error(exc),
                    "issues": diagnostic_payload["issues"],
                    "config_preview": diagnostic_payload["config_preview"],
                },
            )

    def list_tables(self, include_views: bool = True) -> dict[str, Any]:
        config_error = self._config_error()
        if config_error:
            return config_error
        types = ("table", "view") if include_views else ("table",)
        placeholders = ",".join("?" for _ in types)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT name, type FROM sqlite_master WHERE type IN ({placeholders}) AND name NOT LIKE 'sqlite_%' ORDER BY type, name",
                    types,
                ).fetchall()
            tables = [{"name": str(row["name"]), "type": str(row["type"])} for row in rows]
            return format_list_tables(driver=self.driver, tables=tables)
        except Exception as exc:  # noqa: BLE001
            return _failure("database_query_failed", _safe_error(exc))

    def describe_table(self, table: str) -> dict[str, Any]:
        table_error = _validate_identifier(table)
        if table_error:
            return table_error
        config_error = self._config_error()
        if config_error:
            return config_error
        try:
            with self._connect() as connection:
                rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = [
                {
                    "name": str(row["name"]),
                    "type": str(row["type"]),
                    "notnull": bool(row["notnull"]),
                    "default": row["dflt_value"],
                    "pk": bool(row["pk"]),
                }
                for row in rows
            ]
            return format_table_schema(driver=self.driver, table=table, columns=columns)
        except Exception as exc:  # noqa: BLE001
            return _failure("database_query_failed", _safe_error(exc), {"table": table})

    def select_query(self, sql: str, limit: int | None = None) -> dict[str, Any]:
        validation = validate_read_only_sql(sql, allow_sqlite_only=True)
        if not validation.success:
            return validation.error
        safe_sql = validation.sql
        config_error = self._config_error()
        if config_error:
            return config_error
        row_limit = _effective_query_limit(safe_sql, limit, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT)
        try:
            with self._connect() as connection:
                cursor = connection.execute(f"SELECT * FROM ({safe_sql}) LIMIT ?", (row_limit + 1,))
                columns = [description[0] for description in cursor.description or []]
                rows = [_row_to_dict(columns, row) for row in cursor.fetchall()]
            return format_query_result(driver=self.driver, columns=columns, rows=rows, max_rows=row_limit, limited=True, extra={"limit": row_limit})
        except Exception as exc:  # noqa: BLE001
            return _failure("database_query_failed", _safe_error(exc))

    def sample_rows(self, table: str, limit: int | None = None) -> dict[str, Any]:
        table_error = _validate_identifier(table)
        if table_error:
            return table_error
        config_error = self._config_error()
        if config_error:
            return config_error
        row_limit = _bounded_int(limit, DEFAULT_SAMPLE_LIMIT, MAX_SAMPLE_LIMIT)
        try:
            with self._connect() as connection:
                cursor = connection.execute(f'SELECT * FROM "{table}" LIMIT ?', (row_limit + 1,))
                columns = [description[0] for description in cursor.description or []]
                rows = [_row_to_dict(columns, row) for row in cursor.fetchall()]
            return format_sample_rows(driver=self.driver, table=table, columns=columns, rows=rows, max_rows=row_limit, limit=row_limit)
        except Exception as exc:  # noqa: BLE001
            return _failure("database_query_failed", _safe_error(exc), {"table": table})

    def _connect(self) -> sqlite3.Connection:
        path = Path(str(self.path)).expanduser()
        uri_path = path.resolve().as_posix()
        connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def _config_error(self) -> dict[str, Any] | None:
        diagnostic = self._diagnostic()
        if not diagnostic.issues:
            return None
        issue = diagnostic.issues[0]
        return _failure(issue.code, issue.message, {"driver": self.driver, "configured": False, "database_path_preview": _path_preview(self.path)})

    def _diagnostic(self) -> DatabaseConnectionDiagnostic:
        path_text = str(self.path or "").strip()
        issues: list[DatabaseConfigIssue] = []
        preview = {"driver": self.driver, "path_preview": _path_preview(path_text), "read_only": True}
        if not path_text or PLACEHOLDER_PATTERN.fullmatch(path_text):
            issues.append(_issue("database_sqlite_path_missing", "DATABASE_MCP_SQLITE_PATH", "SQLite database path is not configured.", "Set DATABASE_MCP_SQLITE_PATH to an existing SQLite database file."))
        else:
            path = Path(path_text).expanduser()
            if _is_sensitive_path(path):
                issues.append(_issue("database_sqlite_path_sensitive", "DATABASE_MCP_SQLITE_PATH", "SQLite database path is not allowed.", "Choose a normal database file outside .env, .git, .venv, venv, or node_modules."))
            elif not path.exists():
                issues.append(_issue("database_sqlite_path_not_found", "DATABASE_MCP_SQLITE_PATH", "SQLite database file does not exist.", "Set DATABASE_MCP_SQLITE_PATH to an existing SQLite database file."))
            elif path.is_dir():
                issues.append(_issue("database_sqlite_path_is_directory", "DATABASE_MCP_SQLITE_PATH", "SQLite database path points to a directory.", "Set DATABASE_MCP_SQLITE_PATH to a SQLite database file, not a directory."))
        return DatabaseConnectionDiagnostic(
            driver=self.driver,
            configured=not issues,
            read_only=True,
            dependency_available=True,
            issues=issues,
            config_preview=preview,
        )


class MySQLDatabaseBackend:
    """Read-only MySQL backend loaded only when selected."""

    driver = "mysql"

    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.host = env.get("DATABASE_MCP_MYSQL_HOST", "")
        self.user = env.get("DATABASE_MCP_MYSQL_USER", "")
        self.password = env.get("DATABASE_MCP_MYSQL_PASSWORD", "")
        self.database = env.get("DATABASE_MCP_MYSQL_DATABASE", "")
        self.charset = env.get("DATABASE_MCP_MYSQL_CHARSET", DEFAULT_MYSQL_CHARSET) or DEFAULT_MYSQL_CHARSET
        self.port_raw = env.get("DATABASE_MCP_MYSQL_PORT", str(DEFAULT_MYSQL_PORT))
        self.connect_timeout_raw = env.get("DATABASE_MCP_MYSQL_CONNECT_TIMEOUT", str(DEFAULT_MYSQL_CONNECT_TIMEOUT))
        self.max_rows = _env_bounded_int(env.get("DATABASE_MCP_MAX_ROWS"), MAX_QUERY_LIMIT, MAX_QUERY_LIMIT)
        self.max_rows_raw = env.get("DATABASE_MCP_MAX_ROWS", str(MAX_QUERY_LIMIT))

    def status(self) -> dict[str, Any]:
        return self.connection_config()

    def connection_config(self) -> dict[str, Any]:
        diagnostic = self._diagnostic().to_dict()
        issue = diagnostic["issues"][0] if diagnostic["issues"] else None
        diagnostic["host_preview"] = diagnostic["config_preview"].get("host", "")
        diagnostic["database_preview"] = diagnostic["config_preview"].get("database", "")
        diagnostic["error"] = "" if issue is None else issue["message"]
        diagnostic["error_code"] = "" if issue is None else issue["code"]
        return diagnostic

    def test_connection(self) -> dict[str, Any]:
        diagnostic = self._diagnostic()
        if diagnostic.issues:
            payload = diagnostic.to_dict()
            issue = payload["issues"][0]
            return {**payload, "connected": False, "error_code": issue["code"], "error": issue["message"], "message": issue["message"]}
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            payload = diagnostic.to_dict()
            return {**payload, "success": True, "connected": True}
        except Exception as exc:  # noqa: BLE001
            payload = self._diagnostic().to_dict()
            return _failure(
                "database_mysql_connection_failed",
                "MySQL connection failed.",
                {
                    "suggestion": "Please check host, port, user, database, network and read-only permissions.",
                    "connected": False,
                    "driver": self.driver,
                    "issues": payload["issues"],
                    "config_preview": payload["config_preview"],
                    "detail": _safe_error(exc, [self.password]),
                },
            )

    def list_tables(self, include_views: bool = True) -> dict[str, Any]:
        setup_error = self._setup_error()
        if setup_error:
            return setup_error
        query = (
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = DATABASE() ORDER BY table_type, table_name"
        )
        try:
            rows = self._fetch_all(query)
            tables = []
            for row in rows:
                name = _row_get(row, "table_name", 0)
                table_type = _normalize_mysql_table_type(_row_get(row, "table_type", 1))
                if include_views or table_type == "table":
                    tables.append({"name": str(name), "type": table_type})
            return format_list_tables(driver=self.driver, tables=tables)
        except Exception as exc:  # noqa: BLE001
            return _mysql_query_failure(exc, secrets=[self.password])

    def describe_table(self, table: str) -> dict[str, Any]:
        table_error = _validate_identifier(table)
        if table_error:
            return table_error
        setup_error = self._setup_error()
        if setup_error:
            return setup_error
        query = (
            "SELECT column_name, column_type, is_nullable, column_default, column_key "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s "
            "ORDER BY ordinal_position"
        )
        try:
            rows = self._fetch_all(query, (table,))
            columns = [
                {
                    "name": str(_row_get(row, "column_name", 0)),
                    "type": str(_row_get(row, "column_type", 1)),
                    "nullable": str(_row_get(row, "is_nullable", 2)).upper() == "YES",
                    "default": _row_get(row, "column_default", 3),
                    "pk": str(_row_get(row, "column_key", 4)).upper() == "PRI",
                }
                for row in rows
            ]
            return format_table_schema(driver=self.driver, table=table, columns=columns)
        except Exception as exc:  # noqa: BLE001
            return _mysql_query_failure(exc, {"table": table}, secrets=[self.password])

    def select_query(self, sql: str, limit: int | None = None) -> dict[str, Any]:
        validation = validate_read_only_sql(sql)
        if not validation.success:
            return validation.error
        row_limit = _effective_query_limit(validation.sql, limit, self.max_rows, self.max_rows)
        limited_sql = apply_sql_row_limit(validation.sql, row_limit)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(limited_sql)
                    columns = [description[0] for description in cursor.description or []]
                    rows = [_row_to_dict(columns, row) for row in cursor.fetchall()[: row_limit + 1]]
            return format_query_result(driver=self.driver, columns=columns, rows=rows, max_rows=row_limit, limited=True, extra={"limit": row_limit, "sql": limited_sql})
        except Exception as exc:  # noqa: BLE001
            return _mysql_query_failure(exc, secrets=[self.password])

    def sample_rows(self, table: str, limit: int | None = None) -> dict[str, Any]:
        table_error = _validate_identifier(table)
        if table_error:
            return table_error
        setup_error = self._setup_error()
        if setup_error:
            return setup_error
        row_limit = _bounded_int(limit, DEFAULT_SAMPLE_LIMIT, self.max_rows)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT * FROM `{table}` LIMIT %s", (row_limit + 1,))
                    columns = [description[0] for description in cursor.description or []]
                    rows = [_row_to_dict(columns, row) for row in cursor.fetchall()[: row_limit + 1]]
            return format_sample_rows(driver=self.driver, table=table, columns=columns, rows=rows, max_rows=row_limit, limit=row_limit)
        except Exception as exc:  # noqa: BLE001
            return _mysql_query_failure(exc, {"table": table}, secrets=[self.password])

    def _connect(self) -> Any:
        pymysql = self._import_pymysql()
        return pymysql.connect(
            host=self.host.strip(),
            port=self._port(),
            user=self.user.strip(),
            password=self.password,
            database=self.database.strip(),
            charset=self.charset.strip() or DEFAULT_MYSQL_CHARSET,
            connect_timeout=self._connect_timeout(),
        )

    def _setup_error(self) -> dict[str, Any] | None:
        return self._config_error() or self._dependency_error()

    def _config_error(self) -> dict[str, Any] | None:
        config_issues = [issue for issue in self._diagnostic().issues if issue.code != "database_mysql_dependency_missing"]
        if config_issues:
            issue = config_issues[0]
            return _failure(issue.code, issue.message, {"driver": self.driver, "configured": False, "host_preview": _preview(self.host), "database_preview": _preview(self.database)})
        return None

    def _dependency_status(self) -> tuple[bool, dict[str, Any] | None]:
        error = self._dependency_error()
        return error is None, error

    def _dependency_error(self) -> dict[str, Any] | None:
        if not self._dependency_available():
            return _failure(
                "database_mysql_dependency_missing",
                "MySQL driver dependency is missing. Install PyMySQL to enable MySQL Database MCP.",
                {"driver": self.driver, "configured": self._config_error() is None},
            )
        return None

    def _diagnostic(self) -> DatabaseConnectionDiagnostic:
        issues: list[DatabaseConfigIssue] = []
        host_configured = _is_real_config_value(self.host)
        user_configured = _is_real_config_value(self.user)
        database_configured = _is_real_config_value(self.database)
        password_configured = _is_real_config_value(self.password)
        charset = self.charset.strip() or DEFAULT_MYSQL_CHARSET

        if not host_configured:
            issues.append(_issue("database_mysql_host_missing", "DATABASE_MCP_MYSQL_HOST", "MySQL host is not configured.", "Set DATABASE_MCP_MYSQL_HOST."))
        if not user_configured:
            issues.append(_issue("database_mysql_user_missing", "DATABASE_MCP_MYSQL_USER", "MySQL user is not configured.", "Set DATABASE_MCP_MYSQL_USER."))
        if not database_configured:
            issues.append(_issue("database_mysql_database_missing", "DATABASE_MCP_MYSQL_DATABASE", "MySQL database is not configured.", "Set DATABASE_MCP_MYSQL_DATABASE."))
        if not charset:
            issues.append(_issue("database_mysql_charset_missing", "DATABASE_MCP_MYSQL_CHARSET", "MySQL charset is not configured.", "Set DATABASE_MCP_MYSQL_CHARSET to utf8mb4."))
        try:
            port = self._port()
        except ValueError:
            port = DEFAULT_MYSQL_PORT
            issues.append(_issue("database_mysql_port_invalid", "DATABASE_MCP_MYSQL_PORT", "MySQL port must be an integer between 1 and 65535.", "Set DATABASE_MCP_MYSQL_PORT to a value from 1 to 65535."))
        try:
            connect_timeout = self._connect_timeout()
        except ValueError:
            connect_timeout = DEFAULT_MYSQL_CONNECT_TIMEOUT
            issues.append(_issue("database_mysql_connect_timeout_invalid", "DATABASE_MCP_MYSQL_CONNECT_TIMEOUT", "MySQL connect timeout must be an integer between 1 and 120.", "Set DATABASE_MCP_MYSQL_CONNECT_TIMEOUT to a value from 1 to 120."))
        try:
            max_rows = _parse_int_range(self.max_rows_raw, "DATABASE_MCP_MAX_ROWS", 1, MAX_QUERY_LIMIT)
        except ValueError:
            max_rows = MAX_QUERY_LIMIT
            issues.append(_issue("database_max_rows_invalid", "DATABASE_MCP_MAX_ROWS", f"DATABASE_MCP_MAX_ROWS must be an integer between 1 and {MAX_QUERY_LIMIT}.", f"Set DATABASE_MCP_MAX_ROWS to a value from 1 to {MAX_QUERY_LIMIT}."))

        dependency_available = self._dependency_available()
        if not dependency_available:
            issues.append(_issue("database_mysql_dependency_missing", "PyMySQL", "MySQL driver dependency is missing.", "Install PyMySQL to enable MySQL Database MCP."))

        preview = {
            "driver": self.driver,
            "host": _preview(self.host) if host_configured else "",
            "port": port,
            "user": _preview(self.user) if user_configured else "",
            "database": _preview(self.database) if database_configured else "",
            "charset": charset,
            "connect_timeout": connect_timeout,
            "max_rows": max_rows,
            "password_configured": password_configured,
            "password": "[REDACTED]",
            "read_only": True,
        }
        return DatabaseConnectionDiagnostic(
            driver=self.driver,
            configured=not [issue for issue in issues if issue.code != "database_mysql_dependency_missing"],
            read_only=True,
            dependency_available=dependency_available,
            issues=issues,
            config_preview=preview,
        )

    def _dependency_available(self) -> bool:
        try:
            self._import_pymysql()
        except ImportError:
            return False
        return True

    def _import_pymysql(self) -> Any:
        import pymysql  # type: ignore[import-not-found]

        return pymysql

    def _port(self) -> int:
        return _parse_int_range(self.port_raw, "DATABASE_MCP_MYSQL_PORT", 1, 65535)

    def _connect_timeout(self) -> int:
        return _parse_int_range(self.connect_timeout_raw, "DATABASE_MCP_MYSQL_CONNECT_TIMEOUT", 1, 120)

    def _fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return list(cursor.fetchall())


def build_database_backend_from_env(env: dict[str, str] | None = None) -> DatabaseBackend:
    env = os.environ if env is None else env
    driver = str(env.get("DATABASE_MCP_DRIVER", "sqlite") or "sqlite").strip().lower()
    if driver == "sqlite":
        return SQLiteDatabaseBackend(env.get("DATABASE_MCP_SQLITE_PATH"))
    if driver == "mysql":
        return MySQLDatabaseBackend(env)
    return UnsupportedDatabaseBackend(driver)


def main() -> None:
    backend = build_database_backend_from_env()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = _handle_request(request, backend)
            if response is not None:
                _write(response)
        except Exception as exc:  # noqa: BLE001 - MCP server boundary must stay alive.
            print(f"database MCP request failed: {_safe_error(exc)}", file=sys.stderr)
            request_id = _safe_request_id(line)
            if request_id is not None:
                _write(_error(request_id, "internal_error", "Database MCP request failed."))


def _handle_request(request: dict[str, Any], backend: DatabaseBackend) -> dict[str, Any] | None:
    method = str(request.get("method", ""))
    request_id = request.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "database", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        name = str(params.get("name", ""))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        return {"jsonrpc": "2.0", "id": request_id, "result": _call_tool(backend, name, arguments)}
    return _error(request_id, "method_not_found", f"Unsupported MCP method: {method}")


def _call_tool(backend: DatabaseBackend, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    boundary = evaluate_database_mcp_server_boundary(name, arguments)
    if boundary is not None:
        return _tool_result(boundary, is_error=True)
    try:
        if name == "get_database_status":
            result = backend.status()
        elif name == "database_test_connection":
            result = backend.test_connection()
        elif name == "database_get_connection_config":
            result = backend.connection_config()
        elif name == "database_list_tables":
            result = backend.list_tables(include_views=bool(arguments.get("include_views", True)))
        elif name == "database_describe_table":
            result = backend.describe_table(str(arguments.get("table", "")))
        elif name == "database_select_query":
            result = backend.select_query(str(arguments.get("sql", "")), arguments.get("limit"))
        elif name == "database_get_sample_rows":
            result = backend.sample_rows(str(arguments.get("table", "")), arguments.get("limit"))
        else:
            result = _failure("database_tool_not_found", f"Unknown database tool: {name}")
    except Exception as exc:  # noqa: BLE001 - return structured MCP tool error.
        print(f"database tool failed: {name}: {_safe_error(exc)}", file=sys.stderr)
        result = _failure("database_tool_failed", _safe_error(exc))
    return _tool_result(result, is_error=not bool(result.get("success")))


def evaluate_database_mcp_server_boundary(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(name or "")
    lowered = tool_name.lower()
    known_tools = {str(tool.get("name") or "") for tool in TOOLS}
    if tool_name in DATABASE_WRITE_TOOL_NAMES or (lowered.startswith("database_") and any(marker in lowered for marker in DATABASE_WRITE_NAME_MARKERS)):
        return _failure(
            "database_write_tool_not_allowed",
            "Database write tools are blocked by the MCP server boundary.",
            {"reason": "database_write", "tool": tool_name},
        )
    if tool_name not in known_tools:
        return _failure("database_tool_not_found", f"Unknown database tool: {tool_name}", {"reason": "unknown_tool", "tool": tool_name})
    if tool_name == "database_select_query":
        validation = validate_read_only_sql(str(arguments.get("sql", "")), allow_sqlite_only=True)
        if not validation.success:
            return validation.error
    return None


class SqlValidationResult:
    def __init__(self, success: bool, sql: str = "", error: dict[str, Any] | None = None) -> None:
        self.success = success
        self.sql = sql
        self.error = error or {}


def validate_read_only_sql(sql: str, *, allow_sqlite_only: bool = False) -> SqlValidationResult:
    text = str(sql or "").strip()
    reject_code = "database_sql_rejected" if allow_sqlite_only else "database_query_not_read_only"
    if not text:
        return SqlValidationResult(False, error=_failure("database_sql_invalid", "SQL must be a non-empty read-only statement."))
    normalized = normalize_sql_for_safety(
        text,
        mysql_dash_comment_rules=not allow_sqlite_only,
    )
    if normalized.rstrip().endswith(";"):
        normalized = normalized.rstrip()[:-1].strip()
        text = text[:-1].strip()
    if ";" in normalized:
        return SqlValidationResult(
            False,
            error=format_sql_rejection(
                "database_sql_rejected" if allow_sqlite_only else "database_query_multi_statement_blocked",
                "Multiple SQL statements are not allowed.",
                sql,
            ),
        )
    if allow_sqlite_only and not re.match(r"^select\b", normalized, re.IGNORECASE):
        return SqlValidationResult(False, error=format_sql_rejection("database_sql_rejected", "This database tool only allows read-only queries.", sql))
    if not allow_sqlite_only and not READ_ONLY_SQL_PATTERN.match(normalized):
        return SqlValidationResult(False, error=format_sql_rejection("database_query_not_read_only", "This database tool only allows read-only queries.", sql))
    blocked_patterns = (
        r"\binto\s+outfile\b",
        r"\binto\s+dumpfile\b",
        r"\bfor\s+update\b",
        r"\block\s+in\s+share\s+mode\b",
        r"\bload\s+data\b",
    )
    if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in blocked_patterns):
        return SqlValidationResult(False, error=format_sql_rejection(reject_code, "This database tool only allows read-only queries.", sql))
    if DANGEROUS_SQL_PATTERN.search(normalized):
        return SqlValidationResult(False, error=format_sql_rejection(reject_code, "This database tool only allows read-only queries.", sql))
    return SqlValidationResult(True, sql=text)


def normalize_sql_for_safety(
    sql: str,
    *,
    mysql_dash_comment_rules: bool = True,
    _strip: bool = True,
) -> str:
    text = str(sql or "")
    masked = list(text)
    index = 0
    length = len(text)

    def mask(start: int, end: int) -> None:
        for position in range(start, end):
            if masked[position] != "\n":
                masked[position] = " "

    def executable_comment_end(start: int) -> int:
        depth = 1
        position = start
        while position < length - 1:
            marker = text[position]
            if marker in {"'", '"', "`", "["}:
                closer = "]" if marker == "[" else marker
                position += 1
                while position < length:
                    if marker != "[" and text[position] == "\\" and position + 1 < length:
                        position += 2
                        continue
                    if text[position] == closer:
                        if position + 1 < length and text[position + 1] == closer:
                            position += 2
                            continue
                        position += 1
                        break
                    position += 1
                continue
            if text.startswith("/*", position):
                depth += 1
                position += 2
                continue
            if text.startswith("*/", position):
                depth -= 1
                if depth == 0:
                    return position
                position += 2
                continue
            position += 1
        return length

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""
        if char == "'":
            start = index
            index += 1
            while index < length:
                if text[index] == "\\" and index + 1 < length:
                    index += 2
                    continue
                if text[index] == "'":
                    if index + 1 < length and text[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            mask(start, index)
            continue
        if char == '"':
            start = index
            index += 1
            while index < length:
                if text[index] == "\\" and index + 1 < length:
                    index += 2
                    continue
                if text[index] == '"':
                    if index + 1 < length and text[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            mask(start, index)
            continue
        if char == "`":
            start = index
            index += 1
            while index < length:
                if text[index] == "`":
                    if index + 1 < length and text[index + 1] == "`":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            mask(start, index)
            continue
        if char == "[":
            start = index
            index += 1
            while index < length:
                if text[index] == "]":
                    if index + 1 < length and text[index + 1] == "]":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            mask(start, index)
            continue
        if char == "-" and next_char == "-":
            third_index = index + 2
            mysql_comment = (
                third_index >= length
                or ord(text[third_index]) <= 0x20
            )
            if not mysql_dash_comment_rules or mysql_comment:
                start = index
                index += 2
                while index < length and text[index] != "\n":
                    index += 1
                mask(start, index)
                continue
        if char == "#":
            start = index
            index += 1
            while index < length and text[index] != "\n":
                index += 1
            mask(start, index)
            continue
        if char == "/" and next_char == "*":
            executable = text.startswith("/*!", index) or text.startswith("/*M!", index)
            if executable:
                marker_length = 4 if text.startswith("/*M!", index) else 3
                start = index
                index += marker_length
                mask(start, index)
                while index < length and text[index].isdigit():
                    mask(index, index + 1)
                    index += 1
                end = executable_comment_end(index)
                body = normalize_sql_for_safety(
                    text[index:end],
                    mysql_dash_comment_rules=mysql_dash_comment_rules,
                    _strip=False,
                )
                masked[index:end] = body
                if end < length:
                    mask(end, end + 2)
                    index = end + 2
                else:
                    index = end
                continue
            start = index
            index += 2
            while index < length:
                if text[index] == "*" and index + 1 < length and text[index + 1] == "/":
                    index += 2
                    break
                index += 1
            mask(start, index)
            continue
        index += 1
    normalized = "".join(masked)
    return normalized.strip() if _strip else normalized


def apply_sql_row_limit(sql: str, max_rows: int) -> str:
    text = str(sql or "").strip().rstrip(";").strip()
    if re.match(r"^(show|describe|desc)\b", text, re.IGNORECASE):
        return text
    match = LIMIT_PATTERN.search(text)
    if not match:
        return f"{text} LIMIT {max_rows}"
    current = int(match.group(1))
    if current <= max_rows:
        return text
    return f"{text[:match.start(1)]}{max_rows}{text[match.end(1):]}"


def _validate_identifier(value: str) -> dict[str, Any] | None:
    if not IDENTIFIER_PATTERN.fullmatch(str(value or "")):
        return _failure("database_identifier_invalid", "Identifier must match ^[A-Za-z_][A-Za-z0-9_]*$.")
    return None


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _effective_query_limit(sql: str, requested_limit: Any, default: int, maximum: int) -> int:
    requested = _bounded_int(requested_limit, default, maximum)
    match = LIMIT_PATTERN.search(str(sql or ""))
    if not match:
        return requested
    try:
        sql_limit = int(match.group(1))
    except ValueError:
        return requested
    return max(1, min(requested, sql_limit, maximum))


def _env_bounded_int(value: Any, default: int, maximum: int) -> int:
    return _bounded_int(value, default, maximum)


def _parse_int_range(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _row_to_dict(columns: list[str], row: Any) -> dict[str, Any]:
    return {column: row[index] for index, column in enumerate(columns)}


def _row_get(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key) or row.get(key.upper()) or row.get(key.lower())
    return row[index]


def _safe_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return "[binary]"
    if isinstance(value, str):
        return _sanitize_text(value)[:2000]
    return value


def _normalize_mysql_table_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text == "VIEW":
        return "view"
    return "table"


def _mysql_query_failure(exc: Exception, extra: dict[str, Any] | None = None, *, secrets: list[str] | None = None) -> dict[str, Any]:
    payload = {"driver": "mysql", "detail": _safe_error(exc, secrets), **(extra or {})}
    return _failure("database_mysql_query_failed", "MySQL query failed.", payload)


def _tool_result(result: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": _bounded_json(result)}],
        "structuredContent": result,
        "isError": bool(is_error),
    }


def _failure(error_code: str, error: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    sanitized_extra = {key: _sanitize_text(value) if isinstance(value, str) else value for key, value in (extra or {}).items() if not _is_secret_field(key)}
    return {
        "success": False,
        "error_code": error_code,
        "code": error_code,
        "error": _sanitize_text(error),
        "message": _sanitize_text(error),
        **sanitized_extra,
    }


def _bounded_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return json.dumps({"success": False, "error_code": "database_result_truncated", "truncated_text": text[:MAX_TEXT_CHARS]}, ensure_ascii=False)


def _path_preview(value: str) -> str:
    if not str(value or "").strip() or PLACEHOLDER_PATTERN.fullmatch(str(value or "").strip()):
        return ""
    return Path(str(value)).name[:200]


def _preview(value: str) -> str:
    text = str(value or "").strip()
    if not text or PLACEHOLDER_PATTERN.fullmatch(text):
        return ""
    if len(text) <= 8:
        return text
    return f"{text[:5]}***"


def _is_sensitive_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return any(part in parts for part in SENSITIVE_PATH_PARTS)


def _safe_error(exc: Exception, secrets: list[str] | None = None) -> str:
    return _sanitize_text(str(exc) or type(exc).__name__, secrets)


def _sanitize_text(text: str, secrets: list[str] | None = None) -> str:
    value = str(text)
    for secret in [os.environ.get("DATABASE_MCP_MYSQL_PASSWORD", ""), *(secrets or [])]:
        if secret:
            value = value.replace(secret, "[redacted]")
    for marker in ("token", "api_key", "apikey", "secret", "password"):
        value = value.replace(marker, "[redacted]")
        value = value.replace(marker.upper(), "[REDACTED]")
    return value[:1000]


def _issue(code: str, field: str, message: str, suggestion: str, severity: str = "error") -> DatabaseConfigIssue:
    return DatabaseConfigIssue(code=code, field=field, severity=severity, message=message, suggestion=suggestion)


def _next_steps(issues: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    for issue in issues:
        suggestion = str(issue.get("suggestion", "")).strip()
        if suggestion and suggestion not in steps:
            steps.append(suggestion)
    if "Run database_test_connection." not in steps:
        steps.append("Run database_test_connection.")
    return steps


def _is_real_config_value(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and not PLACEHOLDER_PATTERN.fullmatch(text))


def _is_secret_field(key: str) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SECRET_FIELD_MARKERS)


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _safe_request_id(raw_line: str) -> Any:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    return payload.get("id") if isinstance(payload, dict) else None


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
