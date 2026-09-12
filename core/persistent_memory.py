"""SQLite-backed persistent long-term memory."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import DatabaseService, get_database_service
MAX_TASKS = 100
MAX_CONTENT_CHARS = 800
SECRET_RE = re.compile(r"(?i)(api[_-]?key|password|passwd|token|secret)\s*[:=]|sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,}")
POLLUTED_RESEARCH_SUMMARY_MARKERS = (
    "final answer",
    "```",
    "tool_calls",
    "traceback",
    "建议：",
    "该专题页本次未成功读取",
)
COMPACTED_RESEARCH_SUMMARY = "Research task history entry was compacted to remove unsafe final-answer content."
PREFERENCE_HINTS = {"偏好", "风格", "回答风格", "语言", "语气", "中文", "简单", "简洁", "温柔", "温柔中文", "这个偏好", "之前的偏好"}

def utc_now() -> str:
    """Return an ISO UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


class PersistentMemory:
    """User- and project-scoped persistent memory backed by SQLite."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        user_id: str = "default_user",
        project_id: str = "default_project",
        database: DatabaseService | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database_path = self.database.path
        self.user_id = self._ensure_utf8_text(user_id).strip()
        self.project_id = self._ensure_utf8_text(project_id).strip()
        if not self.user_id or not self.project_id:
            raise ValueError("user_id and project_id are required")
        self.user_memory: dict[str, Any] = {}
        self.project_memory: dict[str, Any] = {}
        self.task_history: dict[str, Any] = {}
        self.memory_index: dict[str, Any] = {}
        self.load_errors: list[str] = []
        self._load_blocked = False
        self.load_all()

    def load_all(self) -> dict[str, Any]:
        """Load the current user/project projection from SQLite."""

        try:
            self.load_errors = []
            self._load_blocked = False
            self.database.ensure_ready()
            with self.database.read_transaction() as connection:
                self._load_projection(connection)
            self._refresh_index()
            return {"success": True, "data": self.get_counts()}
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("read/open", exc)

    def save_all(self) -> dict[str, Any]:
        """Reload the projection; SQLite mutations already persist transactionally."""

        return self.load_all()

    def _load_projection(self, connection: sqlite3.Connection) -> None:
        preferences: dict[str, Any] = {}
        preference_rows = connection.execute(
            """
            SELECT key, value, source, time_created, time_updated
            FROM memory_user_preference
            WHERE user_id = ?
            ORDER BY time_created, key
            """,
            (self.user_id,),
        ).fetchall()
        for row in preference_rows:
            preferences[str(row["key"])] = {
                "value": str(row["value"]),
                "source": str(row["source"]),
                "created_at": _ms_to_iso(row["time_created"]),
                "updated_at": _ms_to_iso(row["time_updated"]),
            }

        fact_rows = connection.execute(
            """
            SELECT content, description, source, source_reference, time_created, time_updated
            FROM memory_stable_fact
            WHERE user_id = ?
            ORDER BY time_created, id
            """,
            (self.user_id,),
        ).fetchall()
        facts = [
            {
                "content": str(row["content"]),
                "description": str(row["description"]),
                "source": str(row["source"]),
                "source_reference": str(row["source_reference"]),
                "created_at": _ms_to_iso(row["time_created"]),
                "updated_at": _ms_to_iso(row["time_updated"]),
            }
            for row in fact_rows
        ]
        user_times = [int(row["time_updated"]) for row in (*preference_rows, *fact_rows)]
        self.user_memory = {
            "preferences": preferences,
            "stable_facts": facts,
            "updated_at": _ms_to_iso(max(user_times)) if user_times else "",
        }

        project_rows = connection.execute(
            """
            SELECT project_path, summary, tech_stack, status, source, source_reference,
                   time_created, time_updated
            FROM memory_project_summary
            WHERE user_id = ? AND project_id = ?
            ORDER BY time_created, id
            """,
            (self.user_id, self.project_id),
        ).fetchall()
        projects = [
            {
                "project_path": str(row["project_path"]),
                "summary": str(row["summary"]),
                "tech_stack": _json_list(row["tech_stack"]),
                "status": str(row["status"]),
                "source": str(row["source"]),
                "source_reference": str(row["source_reference"]),
                "created_at": _ms_to_iso(row["time_created"]),
                "updated_at": _ms_to_iso(row["time_updated"]),
            }
            for row in project_rows
        ]
        instruction_rows = connection.execute(
            """
            SELECT content, source, time_created, time_updated
            FROM memory_project_instruction
            WHERE user_id = ? AND project_id = ?
            ORDER BY time_created, id
            """,
            (self.user_id, self.project_id),
        ).fetchall()
        instructions = [
            {
                "content": str(row["content"]),
                "source": str(row["source"]),
                "created_at": _ms_to_iso(row["time_created"]),
                "updated_at": _ms_to_iso(row["time_updated"]),
            }
            for row in instruction_rows
        ]
        self.project_memory = {"instructions": instructions, "projects": projects}

        task_rows = connection.execute(
            """
            SELECT task_id, task_type, goal, result, summary, modified_files,
                   hidden_from_memory_prompt, source, time_created, time_updated
            FROM memory_task_history
            WHERE user_id = ? AND project_id = ?
            ORDER BY time_created, id
            """,
            (self.user_id, self.project_id),
        ).fetchall()
        self.task_history = {
            "tasks": [
                {
                    "task_id": str(row["task_id"] or ""),
                    "task_type": str(row["task_type"]),
                    "goal": str(row["goal"]),
                    "result": str(row["result"]),
                    "summary": str(row["summary"]),
                    "modified_files": _json_list(row["modified_files"]),
                    "hidden_from_memory_prompt": bool(row["hidden_from_memory_prompt"]),
                    "source": str(row["source"]),
                    "created_at": _ms_to_iso(row["time_created"]),
                    "updated_at": _ms_to_iso(row["time_updated"]),
                }
                for row in task_rows
            ]
        }

    def _database_failure(self, operation: str, error: BaseException) -> dict[str, Any]:
        self._load_blocked = True
        self.load_errors = [f"database {operation} failure: {type(error).__name__}"]
        self.user_memory = self._default_user_memory()
        self.project_memory = self._default_project_memory()
        self.task_history = self._default_task_history()
        self.memory_index = self._default_memory_index()
        return {
            "success": False,
            "error": f"Persistent memory database {operation} failure.",
            "data": {"load_errors": list(self.load_errors)},
        }

    def _blocked_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": "Persistent memory database is not currently healthy.",
            "data": {"load_errors": list(self.load_errors)},
        }

    def _finish_write(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        loaded = self.load_all()
        if not loaded.get("success"):
            return loaded
        return {"success": True, "data": data if data is not None else self.get_counts()}

    @classmethod
    def delete_project_scope(
        cls,
        *,
        database_path: Path | str | None,
        user_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """Delete only one user's project-scoped memory in one transaction."""

        safe_user = str(user_id or "").strip()
        safe_project = str(project_id or "").strip()
        if not safe_user or not safe_project:
            return {"success": False, "error": "user_id and project_id are required."}
        database = get_database_service(database_path)
        try:
            database.ensure_ready()
            with database.write_transaction() as connection:
                summary_cursor = connection.execute(
                    "DELETE FROM memory_project_summary WHERE user_id = ? AND project_id = ?",
                    (safe_user, safe_project),
                )
                instruction_cursor = connection.execute(
                    "DELETE FROM memory_project_instruction WHERE user_id = ? AND project_id = ?",
                    (safe_user, safe_project),
                )
                task_cursor = connection.execute(
                    "DELETE FROM memory_task_history WHERE user_id = ? AND project_id = ?",
                    (safe_user, safe_project),
                )
                deleted = {
                    "project_summaries": max(0, int(summary_cursor.rowcount)),
                    "project_instructions": max(0, int(instruction_cursor.rowcount)),
                    "task_history": max(0, int(task_cursor.rowcount)),
                }
        except (OSError, sqlite3.Error) as exc:
            return {
                "success": False,
                "error": "Persistent memory project-scope deletion failed.",
                "data": {"database_errors": [f"{type(exc).__name__}"]},
            }
        return {
            "success": True,
            "data": {
                **deleted,
                "deleted": sum(deleted.values()),
            },
        }

    @classmethod
    def count_all_memory(cls, database_path: Path | str | None = None) -> dict[str, Any]:
        """Count all long-term-memory rows without creating or modifying a database."""

        database = get_database_service(database_path)
        empty = {
            "user_preferences": 0,
            "stable_facts": 0,
            "project_summaries": 0,
            "project_instructions": 0,
            "task_history": 0,
        }
        if not database.path.exists():
            return {"success": True, "data": {"counts": empty, "total": 0}}
        try:
            with database.read_only_transaction() as connection:
                counts = {
                    "user_preferences": int(
                        connection.execute("SELECT COUNT(*) FROM memory_user_preference").fetchone()[0]
                    ),
                    "stable_facts": int(
                        connection.execute("SELECT COUNT(*) FROM memory_stable_fact").fetchone()[0]
                    ),
                    "project_summaries": int(
                        connection.execute("SELECT COUNT(*) FROM memory_project_summary").fetchone()[0]
                    ),
                    "project_instructions": int(
                        connection.execute("SELECT COUNT(*) FROM memory_project_instruction").fetchone()[0]
                    ),
                    "task_history": int(
                        connection.execute("SELECT COUNT(*) FROM memory_task_history").fetchone()[0]
                    ),
                }
        except (OSError, sqlite3.Error) as exc:
            return {
                "success": False,
                "error": "Persistent memory count failed.",
                "data": {"database_errors": [type(exc).__name__]},
            }
        return {"success": True, "data": {"counts": counts, "total": sum(counts.values())}}

    @classmethod
    def reset_all_memory(cls, database_path: Path | str | None = None) -> dict[str, Any]:
        """Delete all long-term-memory rows in one SQLite transaction."""

        database = get_database_service(database_path)
        try:
            database.ensure_ready()
            with database.write_transaction() as connection:
                counts = {
                    "user_preferences": int(
                        connection.execute("SELECT COUNT(*) FROM memory_user_preference").fetchone()[0]
                    ),
                    "stable_facts": int(
                        connection.execute("SELECT COUNT(*) FROM memory_stable_fact").fetchone()[0]
                    ),
                    "project_summaries": int(
                        connection.execute("SELECT COUNT(*) FROM memory_project_summary").fetchone()[0]
                    ),
                    "project_instructions": int(
                        connection.execute("SELECT COUNT(*) FROM memory_project_instruction").fetchone()[0]
                    ),
                    "task_history": int(
                        connection.execute("SELECT COUNT(*) FROM memory_task_history").fetchone()[0]
                    ),
                }
                connection.execute("DELETE FROM memory_user_preference")
                connection.execute("DELETE FROM memory_stable_fact")
                connection.execute("DELETE FROM memory_project_summary")
                connection.execute("DELETE FROM memory_project_instruction")
                connection.execute("DELETE FROM memory_task_history")
        except (OSError, sqlite3.Error) as exc:
            return {
                "success": False,
                "error": "Persistent memory reset failed.",
                "data": {"database_errors": [type(exc).__name__]},
            }
        return {"success": True, "data": {"counts": counts, "deleted": sum(counts.values())}}

    def get_user_memory(self) -> dict[str, Any]:
        """Return user memory."""

        return self.user_memory

    def get_project_memory(self) -> dict[str, Any]:
        """Return project memory."""

        return self.project_memory

    def get_recent_tasks(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent task summaries."""

        tasks = self.task_history.get("tasks", [])
        if not isinstance(tasks, list):
            return []
        return tasks[-max(1, limit) :]

    def safe_task_prompt_summary(self, task: dict[str, Any]) -> str:
        """Return a prompt-safe task summary that never exposes full research answers."""

        return self._task_prompt_summary(task)

    def get_prompt_safe_recent_tasks(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent task entries with prompt-safe summaries."""

        safe_tasks: list[dict[str, Any]] = []
        for task in self.get_recent_tasks(limit=limit):
            if not isinstance(task, dict) or task.get("hidden_from_memory_prompt") is True:
                continue
            item = dict(task)
            item["summary"] = self.safe_task_prompt_summary(task)
            safe_tasks.append(item)
        return safe_tasks

    def add_user_preference(self, key: str, value: str, source: str = "user_explicit") -> dict[str, Any]:
        """Add or update a user preference."""

        if self._is_sensitive(f"{key} {value}"):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        key = self._clean_text(key, 120)
        value = self._clean_text(value, MAX_CONTENT_CHARS)
        if not key or not value:
            return {"success": False, "error": "Preference key and value are required."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        try:
            with self.database.write_transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO memory_user_preference
                        (user_id, key, value, source, time_created, time_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, key) DO UPDATE SET
                        value = excluded.value,
                        source = excluded.source,
                        time_updated = excluded.time_updated
                    """,
                    (self.user_id, key, value, self._clean_text(source, 80), now, now),
                )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write()

    def add_stable_fact(
        self,
        content: str,
        description: str,
        source: str = "user_explicit",
        source_reference: str = "",
    ) -> dict[str, Any]:
        """Add a stable fact if it is not already stored."""

        if self._is_sensitive(f"{content} {description} {source_reference}"):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        content = self._clean_text(content, MAX_CONTENT_CHARS)
        description = self._clean_text(description, 240)
        if not content or not description:
            return {"success": False, "error": "Stable fact content and description are required."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        try:
            with self.database.write_transaction() as connection:
                existed = connection.execute(
                    "SELECT 1 FROM memory_stable_fact WHERE user_id = ? AND content = ?",
                    (self.user_id, content),
                ).fetchone() is not None
                connection.execute(
                    """
                    INSERT INTO memory_stable_fact
                        (user_id, content, description, source, source_reference, time_created, time_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, content) DO UPDATE SET
                        description = excluded.description,
                        source = excluded.source,
                        source_reference = excluded.source_reference,
                        time_updated = excluded.time_updated
                    """,
                    (
                        self.user_id,
                        content,
                        description,
                        self._clean_text(source, 80),
                        self._clean_text(source_reference, 300),
                        now,
                        now,
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write({"updated": existed, "inserted": not existed})

    def update_stable_fact(
        self,
        reference_id: str,
        content: str,
        description: str,
        source: str,
        source_reference: str = "",
    ) -> dict[str, Any]:
        """Update one exact user-scoped fact and return its new opaque reference id."""

        reference_id = self._clean_text(reference_id, 180)
        content = self._clean_text(content, MAX_CONTENT_CHARS)
        description = self._clean_text(description, 240)
        source = self._clean_text(source, 80)
        source_reference = self._clean_text(source_reference, 300)
        if not reference_id or not content or not description or not source:
            return {"success": False, "error": "reference_id, content, description, and source are required."}
        if self._is_sensitive(f"{content} {description} {source_reference}"):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        try:
            with self.database.write_transaction() as connection:
                rows = connection.execute(
                    "SELECT id, content FROM memory_stable_fact WHERE user_id = ?",
                    (self.user_id,),
                ).fetchall()
                target = next(
                    (
                        row
                        for row in rows
                        if _make_reference_id("fact", str(row["content"])) == reference_id
                    ),
                    None,
                )
                if target is None:
                    return {"success": False, "error": "Memory reference was not found."}
                conflict = connection.execute(
                    """
                    SELECT 1 FROM memory_stable_fact
                    WHERE user_id = ? AND content = ? AND id != ?
                    """,
                    (self.user_id, content, int(target["id"])),
                ).fetchone()
                if conflict is not None:
                    return {"success": False, "error": "Stable fact content conflicts with another current-scope fact."}
                connection.execute(
                    """
                    UPDATE memory_stable_fact
                    SET content = ?, description = ?, source = ?, source_reference = ?, time_updated = ?
                    WHERE user_id = ? AND id = ?
                    """,
                    (
                        content,
                        description,
                        source,
                        source_reference,
                        now,
                        self.user_id,
                        int(target["id"]),
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        new_reference_id = _make_reference_id("fact", content)
        return self._finish_write(
            {
                "old_reference_id": reference_id,
                "reference_id": new_reference_id,
                "updated": True,
            }
        )

    def add_project_summary(
        self,
        project_path: str,
        summary: str,
        tech_stack: list[str] | None = None,
        status: str | None = None,
        source: str = "user_explicit",
        source_reference: str = "",
    ) -> dict[str, Any]:
        """Add or update a project summary."""

        payload = f"{project_path} {summary} {tech_stack} {status} {source_reference}"
        if self._is_sensitive(payload):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        project_path = self._clean_text(project_path, 300)
        summary = self._clean_text(summary, MAX_CONTENT_CHARS)
        if not summary:
            return {"success": False, "error": "Project summary is required."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        try:
            with self.database.write_transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT tech_stack, status
                    FROM memory_project_summary
                    WHERE user_id = ? AND project_id = ? AND project_path = ?
                    """,
                    (self.user_id, self.project_id, project_path),
                ).fetchone()
                current_stack = _json_list(existing["tech_stack"]) if existing else []
                current_status = str(existing["status"]) if existing else ""
                effective_stack = [
                    self._ensure_utf8_text(item)
                    for item in (tech_stack or current_stack)
                ]
                effective_status = self._clean_text(status or current_status, 120)
                connection.execute(
                    """
                    INSERT INTO memory_project_summary
                        (user_id, project_id, project_path, summary, tech_stack, status,
                         source, source_reference, time_created, time_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, project_id, project_path) DO UPDATE SET
                        summary = excluded.summary,
                        tech_stack = excluded.tech_stack,
                        status = excluded.status,
                        source = excluded.source,
                        source_reference = excluded.source_reference,
                        time_updated = excluded.time_updated
                    """,
                    (
                        self.user_id,
                        self.project_id,
                        project_path,
                        summary,
                        self._ensure_utf8_text(json.dumps(effective_stack, ensure_ascii=False)),
                        effective_status,
                        self._clean_text(source, 80),
                        self._clean_text(source_reference, 300),
                        now,
                        now,
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write()

    def add_project_instruction(self, content: str, source: str = "user_explicit") -> dict[str, Any]:
        """Save an explicit instruction that applies to the current project."""

        if self._is_sensitive(content):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        content = self._clean_text(content, MAX_CONTENT_CHARS)
        if not content:
            return {"success": False, "error": "Project instruction content is required."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        try:
            with self.database.write_transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO memory_project_instruction
                        (user_id, project_id, content, source, time_created, time_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, project_id, content) DO NOTHING
                    """,
                    (self.user_id, self.project_id, content, self._clean_text(source, 80), now, now),
                )
                deduplicated = cursor.rowcount == 0
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write({"deduplicated": True} if deduplicated else None)

    def update_project_instruction(
        self,
        old_content: str,
        new_content: str,
        source: str = "user_explicit",
    ) -> dict[str, Any]:
        """Replace one exact current-project instruction without creating duplicates."""

        old_content = self._clean_text(old_content, MAX_CONTENT_CHARS)
        new_content = self._clean_text(new_content, MAX_CONTENT_CHARS)
        source = self._clean_text(source, 80)
        if not old_content or not new_content:
            return {"success": False, "error": "old_content and new_content are required."}
        if self._is_sensitive(new_content):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        try:
            with self.database.write_transaction() as connection:
                target = connection.execute(
                    """
                    SELECT id FROM memory_project_instruction
                    WHERE user_id = ? AND project_id = ? AND content = ?
                    """,
                    (self.user_id, self.project_id, old_content),
                ).fetchone()
                if target is None:
                    return {"success": False, "error": "Project instruction was not found."}
                conflict = connection.execute(
                    """
                    SELECT id FROM memory_project_instruction
                    WHERE user_id = ? AND project_id = ? AND content = ? AND id != ?
                    """,
                    (self.user_id, self.project_id, new_content, int(target["id"])),
                ).fetchone()
                if conflict is not None:
                    return {"success": False, "error": "The replacement project instruction already exists."}
                connection.execute(
                    """
                    UPDATE memory_project_instruction
                    SET content = ?, source = ?, time_updated = ?
                    WHERE user_id = ? AND project_id = ? AND id = ?
                    """,
                    (
                        new_content,
                        source,
                        now,
                        self.user_id,
                        self.project_id,
                        int(target["id"]),
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write({"updated": True})

    def delete_user_preference(self, key: str) -> dict[str, Any]:
        """Delete one exact user-scoped preference key."""

        key = self._clean_text(key, 80)
        if not key:
            return {"success": False, "error": "Preference key is required."}
        if self._load_blocked:
            return self._blocked_result()
        try:
            with self.database.write_transaction() as connection:
                cursor = connection.execute(
                    "DELETE FROM memory_user_preference WHERE user_id = ? AND key = ?",
                    (self.user_id, key),
                )
                deleted = max(0, int(cursor.rowcount))
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        if deleted == 0:
            return {"success": False, "error": "User preference was not found."}
        return self._finish_write({"deleted": deleted, "key": key})

    def delete_project_instruction(self, content: str) -> dict[str, Any]:
        """Delete one exact current-project instruction."""

        content = self._clean_text(content, MAX_CONTENT_CHARS)
        if not content:
            return {"success": False, "error": "Project instruction content is required."}
        if self._load_blocked:
            return self._blocked_result()
        try:
            with self.database.write_transaction() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM memory_project_instruction
                    WHERE user_id = ? AND project_id = ? AND content = ?
                    """,
                    (self.user_id, self.project_id, content),
                )
                deleted = max(0, int(cursor.rowcount))
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        if deleted == 0:
            return {"success": False, "error": "Project instruction was not found."}
        return self._finish_write({"deleted": deleted, "content": content})

    def add_task_summary(self, task_summary: dict[str, Any]) -> dict[str, Any]:
        """Append a compact task summary, keeping only the latest 100."""

        cleaned = self._clean_task_summary(task_summary)
        if self._is_sensitive(json.dumps(cleaned, ensure_ascii=False)):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        if self._load_blocked:
            return self._blocked_result()
        now = _now_ms()
        task_id = cleaned.get("task_id") or None
        created = _iso_to_ms(cleaned.get("created_at"), now)
        try:
            with self.database.write_transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO memory_task_history
                        (user_id, project_id, task_id, task_type, goal, result, summary,
                         modified_files, hidden_from_memory_prompt, source, time_created, time_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, project_id, task_id) WHERE task_id IS NOT NULL DO NOTHING
                    """,
                    (
                        self.user_id,
                        self.project_id,
                        task_id,
                        cleaned.get("task_type", ""),
                        cleaned.get("goal", ""),
                        cleaned.get("result", ""),
                        cleaned.get("summary", ""),
                        self._ensure_utf8_text(
                            json.dumps(cleaned.get("modified_files", []), ensure_ascii=False)
                        ),
                        1 if cleaned.get("hidden_from_memory_prompt") else 0,
                        cleaned.get("source", ""),
                        created,
                        now,
                    ),
                )
                deduplicated = cursor.rowcount == 0
                connection.execute(
                    """
                    DELETE FROM memory_task_history
                    WHERE id IN (
                        SELECT id FROM memory_task_history
                        WHERE user_id = ? AND project_id = ?
                        ORDER BY time_created DESC, id DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (self.user_id, self.project_id, MAX_TASKS),
                )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write({"deduplicated": True} if deduplicated else None)

    def delete_memory_reference(self, reference_id: str) -> dict[str, Any]:
        """Delete one exact scoped background reference in one transaction."""

        reference_id = self._clean_text(reference_id, 180)
        if not reference_id:
            return {"success": False, "error": "reference_id is required."}
        if self._load_blocked:
            return self._blocked_result()
        deleted_type = ""
        try:
            with self.database.write_transaction() as connection:
                if reference_id.startswith("fact:"):
                    rows = connection.execute(
                        "SELECT id, content FROM memory_stable_fact WHERE user_id = ?",
                        (self.user_id,),
                    ).fetchall()
                    target = next(
                        (row for row in rows if _make_reference_id("fact", str(row["content"])) == reference_id),
                        None,
                    )
                    if target is not None:
                        connection.execute(
                            "DELETE FROM memory_stable_fact WHERE user_id = ? AND id = ?",
                            (self.user_id, int(target["id"])),
                        )
                        deleted_type = "stable_fact"
                elif reference_id.startswith("project:"):
                    rows = connection.execute(
                        """
                        SELECT id, project_path FROM memory_project_summary
                        WHERE user_id = ? AND project_id = ?
                        """,
                        (self.user_id, self.project_id),
                    ).fetchall()
                    target = next(
                        (
                            row
                            for row in rows
                            if _make_reference_id("project", str(row["project_path"])) == reference_id
                        ),
                        None,
                    )
                    if target is not None:
                        connection.execute(
                            """
                            DELETE FROM memory_project_summary
                            WHERE user_id = ? AND project_id = ? AND id = ?
                            """,
                            (self.user_id, self.project_id, int(target["id"])),
                        )
                        deleted_type = "project_summary"
                elif reference_id.startswith("task:"):
                    rows = connection.execute(
                        """
                        SELECT id, task_id, task_type, goal, result, summary, modified_files,
                               hidden_from_memory_prompt, source, time_created, time_updated
                        FROM memory_task_history
                        WHERE user_id = ? AND project_id = ?
                        """,
                        (self.user_id, self.project_id),
                    ).fetchall()
                    target = next(
                        (
                            row
                            for row in rows
                            if _make_reference_id("task", _task_row_identity(row)) == reference_id
                        ),
                        None,
                    )
                    if target is not None:
                        connection.execute(
                            """
                            DELETE FROM memory_task_history
                            WHERE user_id = ? AND project_id = ? AND id = ?
                            """,
                            (self.user_id, self.project_id, int(target["id"])),
                        )
                        deleted_type = "task_history"
                if not deleted_type:
                    return {"success": False, "error": "Memory reference was not found."}
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write(
            {
                "deleted": 1,
                "memory_type": deleted_type,
                "reference_id": reference_id,
            }
        )

    def clear_memory_type(self, memory_type: str) -> dict[str, Any]:
        """Clear one allowed category in the current SQL scope."""

        normalized = (memory_type or "").lower()
        if normalized not in {
            "user_preferences",
            "stable_facts",
            "task_history",
            "project_memory",
            "project_instructions",
        }:
            return {"success": False, "error": "memory_type must be one of: user_preferences, stable_facts, task_history, project_memory, project_instructions."}
        if self._load_blocked:
            return self._blocked_result()
        try:
            with self.database.write_transaction() as connection:
                if normalized == "user_preferences":
                    row = connection.execute(
                        "SELECT COUNT(*) AS count FROM memory_user_preference WHERE user_id = ?",
                        (self.user_id,),
                    ).fetchone()
                    count = int(row["count"] if row else 0)
                    connection.execute("DELETE FROM memory_user_preference WHERE user_id = ?", (self.user_id,))
                elif normalized == "stable_facts":
                    row = connection.execute(
                        "SELECT COUNT(*) AS count FROM memory_stable_fact WHERE user_id = ?",
                        (self.user_id,),
                    ).fetchone()
                    count = int(row["count"] if row else 0)
                    connection.execute("DELETE FROM memory_stable_fact WHERE user_id = ?", (self.user_id,))
                elif normalized == "task_history":
                    row = connection.execute(
                        """
                        SELECT COUNT(*) AS count FROM memory_task_history
                        WHERE user_id = ? AND project_id = ?
                        """,
                        (self.user_id, self.project_id),
                    ).fetchone()
                    count = int(row["count"] if row else 0)
                    connection.execute(
                        "DELETE FROM memory_task_history WHERE user_id = ? AND project_id = ?",
                        (self.user_id, self.project_id),
                    )
                elif normalized == "project_instructions":
                    row = connection.execute(
                        """
                        SELECT COUNT(*) AS count FROM memory_project_instruction
                        WHERE user_id = ? AND project_id = ?
                        """,
                        (self.user_id, self.project_id),
                    ).fetchone()
                    count = int(row["count"] if row else 0)
                    connection.execute(
                        "DELETE FROM memory_project_instruction WHERE user_id = ? AND project_id = ?",
                        (self.user_id, self.project_id),
                    )
                else:
                    summary_row = connection.execute(
                        """
                        SELECT COUNT(*) AS count FROM memory_project_summary
                        WHERE user_id = ? AND project_id = ?
                        """,
                        (self.user_id, self.project_id),
                    ).fetchone()
                    instruction_row = connection.execute(
                        """
                        SELECT COUNT(*) AS count FROM memory_project_instruction
                        WHERE user_id = ? AND project_id = ?
                        """,
                        (self.user_id, self.project_id),
                    ).fetchone()
                    count = int(summary_row["count"] if summary_row else 0) + int(
                        instruction_row["count"] if instruction_row else 0
                    )
                    connection.execute(
                        "DELETE FROM memory_project_summary WHERE user_id = ? AND project_id = ?",
                        (self.user_id, self.project_id),
                    )
                    connection.execute(
                        "DELETE FROM memory_project_instruction WHERE user_id = ? AND project_id = ?",
                        (self.user_id, self.project_id),
                    )
        except (OSError, sqlite3.Error) as exc:
            return self._database_failure("write", exc)
        return self._finish_write({"cleared": count, "memory_type": normalized})

    def format_for_prompt(
        self,
        max_chars: int = 2000,
        include_task_history: bool = True,
        include_preferences: bool = True,
        include_project_memory: bool = True,
        mode: str = "default",
    ) -> str:
        """Format explicit instructions and the available reference catalog."""

        del include_task_history, include_preferences, include_project_memory, mode
        sections: list[str] = []
        instructions = self.format_instruction_context(max_chars=max_chars)
        if instructions:
            sections.append(instructions)
        remaining = max_chars - len("\n\n".join(sections))
        if remaining > 200:
            guidance = self.format_reference_guidance(max_chars=remaining)
            if guidance:
                sections.append(guidance)
        return "\n\n".join(sections)[:max_chars]

    def format_instruction_context(self, max_chars: int | None = 1600) -> str:
        """Return only explicit user and project instructions."""

        lines = [
            "Persistent instructions:",
            "Apply these instructions when relevant. The current user request has higher priority.",
        ]
        preferences = self.user_memory.get("preferences", {})
        if isinstance(preferences, dict) and preferences:
            lines.append("User instructions:")
            for key, item in preferences.items():
                value = item.get("value") if isinstance(item, dict) else item
                lines.append(f"- {key}: {value}")
        project_instructions = self.project_memory.get("instructions", [])
        if isinstance(project_instructions, list) and project_instructions:
            lines.append("Project instructions:")
            for item in project_instructions:
                if isinstance(item, dict) and item.get("content"):
                    lines.append(f"- {item.get('content')}")
        if len(lines) == 2:
            return ""
        text = "\n".join(lines)
        if max_chars is not None and len(text) > max_chars:
            return text[:max_chars] + "\n... [persistent instructions truncated]"
        return text

    def format_reference_guidance(self, max_chars: int | None = 2000) -> str:
        """Advertise available background types without selecting records."""

        counts = self.get_memory_reference_counts()
        descriptions = {
            "stable_fact": ("user", "Durable user background facts."),
            "project_summary": ("project", "Stored summaries for the current project."),
            "task_history": ("project", "Previous task summaries from the current project."),
        }
        available = [(name, count) for name, count in counts.items() if count > 0]
        if not available:
            return ""
        lines = [
            "Memory references are available as optional background.",
            "<available_memory_reference_types>",
        ]
        for name, count in available:
            scope, description = descriptions[name]
            lines.extend(
                [
                    "  <reference_type>",
                    f"    <name>{name}</name>",
                    f"    <scope>{scope}</scope>",
                    f"    <count>{count}</count>",
                    f"    <description>{description}</description>",
                    "  </reference_type>",
                ]
            )
        lines.extend(
            [
                "</available_memory_reference_types>",
                "Use search_memory_references when prior background may help. "
                "Use read_memory_reference to inspect an exact result before relying on it.",
            ]
        )
        text = "\n".join(lines)
        if max_chars is not None and len(text) > max_chars:
            return text[:max_chars] + "\n... [memory references truncated]"
        return text

    def get_memory_reference_counts(self) -> dict[str, int]:
        """Count model-searchable background records in the current scope."""

        facts = self.user_memory.get("stable_facts", [])
        projects = self.project_memory.get("projects", [])
        tasks = self.task_history.get("tasks", [])
        return {
            "stable_fact": sum(
                1
                for item in (facts if isinstance(facts, list) else [])
                if isinstance(item, dict) and item.get("content")
            ),
            "project_summary": sum(
                1
                for item in (projects if isinstance(projects, list) else [])
                if isinstance(item, dict) and item.get("summary")
            ),
            "task_history": sum(
                1
                for item in (tasks if isinstance(tasks, list) else [])
                if isinstance(item, dict)
                and item.get("hidden_from_memory_prompt") is not True
            ),
        }

    def search_memory_references(self, query: str = "", limit: int = 20) -> dict[str, Any]:
        """Return transparent lexical matches for a model-provided query."""

        cleaned_query = self._clean_text(query, 300)
        query_terms = _tokens(cleaned_query)
        matches: list[dict[str, Any]] = []
        for reference in self._memory_references():
            searchable = " ".join(
                str(reference.get(key) or "")
                for key in ("description", "content", "memory_type", "scope", "source")
            ).lower()
            matched_terms = [term for term in query_terms if term.lower() in searchable]
            if query_terms and not matched_terms:
                continue
            summary = {key: value for key, value in reference.items() if key != "content"}
            summary["matched_terms"] = matched_terms
            matches.append(summary)
        safe_limit = min(max(int(limit or 20), 1), 100)
        return {
            "success": True,
            "data": {
                "query": cleaned_query,
                "match_mode": "literal_term",
                "count": min(len(matches), safe_limit),
                "references": matches[:safe_limit],
            },
        }

    def read_memory_reference(self, reference_id: str) -> dict[str, Any]:
        """Read one exact memory reference together with its provenance."""

        normalized = self._clean_text(reference_id, 180)
        for reference in self._memory_references():
            if reference.get("reference_id") == normalized:
                return {"success": True, "data": {"reference": reference}}
        return {"success": False, "error": "Memory reference was not found."}

    def get_counts(self) -> dict[str, int]:
        """Return memory counts."""

        preferences = self.user_memory.get("preferences", {})
        facts = self.user_memory.get("stable_facts", [])
        projects = self.project_memory.get("projects", [])
        instructions = self.project_memory.get("instructions", [])
        tasks = self.task_history.get("tasks", [])
        return {
            "user_memories": len(preferences if isinstance(preferences, dict) else {}) + len(facts if isinstance(facts, list) else []),
            "projects": len(projects if isinstance(projects, list) else []),
            "project_instructions": len(instructions if isinstance(instructions, list) else []),
            "tasks": len(tasks if isinstance(tasks, list) else []),
        }

    def _memory_references(self) -> list[dict[str, Any]]:
        """Build stable, read-only references from stored background records."""

        references: list[dict[str, Any]] = []
        facts = self.user_memory.get("stable_facts", [])
        if isinstance(facts, list):
            for item in facts:
                if not isinstance(item, dict) or not item.get("content"):
                    continue
                content = str(item.get("content") or "")
                references.append(
                    self._reference_record(
                        prefix="fact",
                        identity=content,
                        memory_type="stable_fact",
                        scope="user",
                        content=json.dumps(item, ensure_ascii=False, sort_keys=True),
                        source=str(item.get("source_reference") or item.get("source") or ""),
                        updated_at=str(item.get("updated_at") or item.get("created_at") or ""),
                        discovery_metadata={
                            "description": str(item.get("description") or ""),
                        },
                    )
                )
        projects = self.project_memory.get("projects", [])
        if isinstance(projects, list):
            for item in projects:
                if not isinstance(item, dict) or not item.get("summary"):
                    continue
                project_path = str(item.get("project_path") or "current project")
                content = json.dumps(item, ensure_ascii=False, sort_keys=True)
                references.append(
                    self._reference_record(
                        prefix="project",
                        identity=project_path,
                        memory_type="project_summary",
                        scope="project",
                        content=content,
                        source=str(item.get("source_reference") or item.get("source") or ""),
                        updated_at=str(item.get("updated_at") or item.get("created_at") or ""),
                        discovery_metadata={
                            "project_path": project_path,
                            "status": str(item.get("status") or ""),
                            "tech_stack": list(item.get("tech_stack") or []),
                        },
                    )
                )
        tasks = self.task_history.get("tasks", [])
        if isinstance(tasks, list):
            for item in tasks:
                if not isinstance(item, dict) or item.get("hidden_from_memory_prompt") is True:
                    continue
                task_id = str(item.get("task_id") or "")
                references.append(
                    self._reference_record(
                        prefix="task",
                        identity=_task_reference_identity(item),
                        memory_type="task_history",
                        scope="project",
                        content=json.dumps(item, ensure_ascii=False, sort_keys=True),
                        source=str(item.get("source") or ""),
                        updated_at=str(item.get("updated_at") or item.get("created_at") or ""),
                        discovery_metadata={
                            "task_id": task_id,
                            "task_type": str(item.get("task_type") or ""),
                            "goal": str(item.get("goal") or ""),
                            "result": str(item.get("result") or ""),
                        },
                    )
                )
        return references

    @staticmethod
    def _reference_record(
        *,
        prefix: str,
        identity: str,
        memory_type: str,
        scope: str,
        content: str,
        source: str,
        updated_at: str,
        discovery_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "reference_id": _make_reference_id(prefix, identity),
            "memory_type": memory_type,
            "scope": scope,
            "content": content,
            "source": source,
            "updated_at": updated_at,
            **discovery_metadata,
        }

    def _refresh_index(self) -> None:
        self.memory_index = {
            "version": 2,
            "last_updated": utc_now(),
            "counts": self.get_counts(),
        }

    @staticmethod
    def _default_user_memory() -> dict[str, Any]:
        return {"preferences": {}, "stable_facts": [], "updated_at": ""}

    @staticmethod
    def _default_project_memory() -> dict[str, Any]:
        return {"instructions": [], "projects": []}

    @staticmethod
    def _default_task_history() -> dict[str, Any]:
        return {"tasks": []}

    @staticmethod
    def _default_memory_index() -> dict[str, Any]:
        return {
            "version": 2,
            "last_updated": "",
            "counts": {
                "user_memories": 0,
                "projects": 0,
                "project_instructions": 0,
                "tasks": 0,
            },
        }

    @staticmethod
    def _ensure_utf8_text(value: Any) -> str:
        """Return deterministic text that strict UTF-8 encoders can persist."""

        text = str(value or "")
        return text.encode("utf-8", errors="backslashreplace").decode("utf-8")

    @classmethod
    def _clean_text(cls, text: Any, limit: int) -> str:
        normalized = cls._ensure_utf8_text(text)
        return " ".join(normalized.split())[:limit]

    @staticmethod
    def _is_sensitive(text: str) -> bool:
        return bool(SECRET_RE.search(text or ""))

    def _clean_task_summary(self, task_summary: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        task_type = self._clean_text(task_summary.get("task_type"), 40)
        summary = self._clean_text(task_summary.get("summary"), MAX_CONTENT_CHARS)
        if task_type == "research" and self._should_compact_research_summary(summary):
            summary = COMPACTED_RESEARCH_SUMMARY
        return {
            "task_id": self._clean_text(task_summary.get("task_id"), 80),
            "task_type": task_type,
            "goal": self._clean_text(task_summary.get("goal"), 300),
            "result": self._clean_text(task_summary.get("result"), 40),
            "summary": summary,
            "modified_files": [self._clean_text(item, 260) for item in task_summary.get("modified_files", [])[:20]],
            "hidden_from_memory_prompt": (
                task_summary.get("hidden_from_memory_prompt") is True
            ),
            "created_at": self._clean_text(task_summary.get("created_at") or now, 80),
            "updated_at": now,
            "source": self._clean_text(task_summary.get("source") or "task_record", 80),
        }

    def _task_prompt_summary(self, task: dict[str, Any]) -> str:
        summary = self._clean_text(task.get("summary") or task.get("goal"), 300)
        if self._should_compact_research_summary(summary):
            return COMPACTED_RESEARCH_SUMMARY
        return summary

    def _should_compact_research_summary(self, summary: str) -> bool:
        text = str(summary or "")
        return self._has_polluted_research_content(text) or _url_count(text) > 1

    @staticmethod
    def _has_polluted_research_content(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(marker.lower() in lowered for marker in POLLUTED_RESEARCH_SUMMARY_MARKERS)

def _tokens(text: str) -> set[str]:
    tokens = {hint for hint in PREFERENCE_HINTS if hint in text}
    for piece in re.split(r"[\s,，。:：/]+", text):
        if piece:
            tokens.add(piece.lower())
    return tokens


def _make_reference_id(prefix: str, identity: str) -> str:
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _task_reference_identity(task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "")
    if task_id:
        return task_id
    payload = {
        key: task.get(key)
        for key in (
            "task_type",
            "goal",
            "result",
            "summary",
            "modified_files",
            "hidden_from_memory_prompt",
            "source",
            "created_at",
            "updated_at",
        )
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _task_row_identity(row: sqlite3.Row) -> str:
    return _task_reference_identity(
        {
            "task_id": str(row["task_id"] or ""),
            "task_type": str(row["task_type"]),
            "goal": str(row["goal"]),
            "result": str(row["result"]),
            "summary": str(row["summary"]),
            "modified_files": _json_list(row["modified_files"]),
            "hidden_from_memory_prompt": bool(row["hidden_from_memory_prompt"]),
            "source": str(row["source"]),
            "created_at": _ms_to_iso(row["time_created"]),
            "updated_at": _ms_to_iso(row["time_updated"]),
        }
    )


def _url_count(text: str) -> int:
    return len(re.findall(r"https?://\S+", str(text or "")))


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _ms_to_iso(value: Any) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def _iso_to_ms(value: Any, default: int) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return default


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
