"""Smoke checks for strict UTF-8-safe PersistentMemory TEXT persistence."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.persistent_memory import PersistentMemory


SURROGATE = "\udce9"


def _assert_utf8(value: Any) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_utf8(key)
            _assert_utf8(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_utf8(item)


def main() -> None:
    escaped = PersistentMemory._ensure_utf8_text(f"abc{SURROGATE}def")
    assert escaped == r"abc\udce9def"
    _assert_utf8(escaped)

    normal = "中文测试 café 日本語"
    assert PersistentMemory._ensure_utf8_text(normal) == normal
    _assert_utf8(normal)

    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "horizon.db"
        memory = PersistentMemory(
            database_path=database_path,
            user_id=f"user{SURROGATE}",
            project_id=f"project{SURROGATE}",
        )

        assert memory.add_user_preference(
            f"style{SURROGATE}",
            f"concise{SURROGATE}",
        )["success"]
        assert memory.add_stable_fact(
            f"fact content{SURROGATE}",
            f"fact description{SURROGATE}",
            source="verified_observation",
            source_reference=f"call{SURROGATE}",
        )["success"]
        assert memory.add_project_summary(
            f"/project/{SURROGATE}",
            f"project summary{SURROGATE}",
            tech_stack=["Python", f"SQLite{SURROGATE}"],
            status=f"active{SURROGATE}",
            source_reference=f"summary-call{SURROGATE}",
        )["success"]
        assert memory.add_project_instruction(f"instruction{SURROGATE}")["success"]
        assert memory.add_task_summary(
            {
                "task_id": f"task{SURROGATE}",
                "task_type": f"coding{SURROGATE}",
                "goal": f"goal{SURROGATE}",
                "result": f"completed{SURROGATE}",
                "summary": f"direct answer summary{SURROGATE}",
                "modified_files": [f"core/example{SURROGATE}.py"],
                "source": f"task_record{SURROGATE}",
            }
        )["success"]

        assert memory.load_all()["success"]
        _assert_utf8(memory.user_id)
        _assert_utf8(memory.project_id)
        _assert_utf8(memory.get_user_memory())
        _assert_utf8(memory.get_project_memory())
        _assert_utf8(memory.get_recent_tasks(limit=10))

        with memory.database.read_transaction() as connection:
            text_columns = {
                "memory_user_preference": ("user_id", "key", "value", "source"),
                "memory_stable_fact": (
                    "user_id", "content", "description", "source", "source_reference"
                ),
                "memory_project_summary": (
                    "user_id", "project_id", "project_path", "summary", "tech_stack",
                    "status", "source", "source_reference",
                ),
                "memory_project_instruction": (
                    "user_id", "project_id", "content", "source"
                ),
                "memory_task_history": (
                    "user_id", "project_id", "task_id", "task_type", "goal", "result",
                    "summary", "modified_files", "source",
                ),
            }
            for table, columns in text_columns.items():
                rows = connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table}"
                ).fetchall()
                assert rows, table
                for row in rows:
                    for column in columns:
                        if row[column] is not None:
                            _assert_utf8(str(row[column]))

    print("smoke_persistent_memory_utf8_boundary ok")


if __name__ == "__main__":
    main()
