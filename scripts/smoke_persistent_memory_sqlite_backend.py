"""Focused smoke for the SQLite persistent-memory backend."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import DatabaseService, get_database_service, get_default_database_path
from core.persistent_memory import PersistentMemory
from core.workspace import WorkspaceManager


TABLES = {
    "memory_user_preference",
    "memory_stable_fact",
    "memory_project_summary",
    "memory_project_instruction",
    "memory_task_history",
}


def _memory(database_path: Path, user_id: str, project_id: str) -> PersistentMemory:
    return PersistentMemory(
        database_path=database_path,
        user_id=user_id,
        project_id=project_id,
    )


def test_database_foundation(database_path: Path) -> None:
    memory = _memory(database_path, "foundation-user", "foundation-project")
    assert memory.load_all().get("success") is True
    assert database_path.exists()
    with memory.database.connect() as connection:
        assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA cache_size").fetchone()[0] == -64000
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        indexes = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        stable_fact_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(memory_stable_fact)").fetchall()
        }
    assert TABLES.issubset(tables)
    assert "description" in stable_fact_columns
    assert "migration" not in tables
    assert {
        "memory_stable_fact_user_idx",
        "memory_project_summary_scope_idx",
        "memory_project_instruction_scope_idx",
        "memory_task_history_scope_time_idx",
        "memory_task_history_scope_task_id_unique",
    }.issubset(indexes)


def test_database_service_lifecycle(database_path: Path) -> None:
    first = get_database_service(database_path)
    second = get_database_service(database_path.resolve())
    assert first is second
    memory = _memory(database_path, "lifecycle-user", "lifecycle-project")
    assert first.is_ready is True
    with patch.object(first, "initialize", side_effect=AssertionError("schema initialization repeated")):
        for _ in range(3):
            assert memory.load_all().get("success") is True

    retry_path = database_path.with_name("retry-horizon.db")
    retry_service = get_database_service(retry_path)

    @contextmanager
    def failed_initialization() -> Any:
        raise sqlite3.OperationalError("forced initialization failure")
        yield  # pragma: no cover

    with patch.object(retry_service, "write_transaction", failed_initialization):
        recovering = PersistentMemory(
            database=retry_service,
            user_id="retry-user",
            project_id="retry-project",
        )
        assert recovering._load_blocked is True
        assert retry_service.is_ready is False
    assert recovering.load_all().get("success") is True
    assert retry_service.is_ready is True


def test_reader_writer_coexistence(database_path: Path) -> None:
    memory = _memory(database_path, "wal-user", "wal-project")
    assert memory.add_user_preference("committed", "VISIBLE").get("success") is True
    writer_started = threading.Event()
    release_writer = threading.Event()
    writer_errors: list[BaseException] = []

    def hold_writer() -> None:
        connection = memory.database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            writer_started.set()
            if not release_writer.wait(timeout=10):
                raise TimeoutError("reader did not release writer")
            connection.rollback()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the smoke.
            writer_errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=hold_writer)
    thread.start()
    assert writer_started.wait(timeout=10)
    started_at = time.monotonic()
    try:
        loaded = memory.load_all()
    finally:
        release_writer.set()
        thread.join(timeout=10)
    elapsed = time.monotonic() - started_at
    assert not writer_errors and not thread.is_alive()
    assert loaded.get("success") is True
    assert memory.get_user_memory()["preferences"]["committed"]["value"] == "VISIBLE"
    assert elapsed < 2.0, f"reader waited too long behind active writer: {elapsed:.3f}s"


def test_database_path_authority(root: Path) -> None:
    injected = root / "database" / "test-horizon.db"
    manager_a = WorkspaceManager(root / "workspace-a", database_path=injected)
    manager_b = WorkspaceManager(root / "workspace-b", database_path=injected)
    assert manager_a.root_dir != manager_b.root_dir
    assert manager_a.database_path == manager_b.database_path == injected.resolve()

    custom_root_manager = WorkspaceManager(root / "custom-workspace-root")
    assert custom_root_manager.database_path == get_default_database_path()
    assert custom_root_manager.database_path != (custom_root_manager.root_dir.parent / "horizon.db").resolve()


def test_project_deletion_lifecycle(root: Path, database_path: Path) -> None:
    manager = WorkspaceManager(root / "workspaces", database_path=database_path)
    project_a_context = manager.get_context("deletion-user", "project-a")
    manager.get_context("deletion-user", "project-b")
    manager.get_context("other-user", "project-a")
    project_a = _memory(database_path, "deletion-user", "project-a")
    project_b = _memory(database_path, "deletion-user", "project-b")
    other_user = _memory(database_path, "other-user", "project-a")

    assert project_a.add_user_preference("shared", "USER_SHARED").get("success") is True
    assert project_a.add_stable_fact("USER_FACT", "Shared user fact").get("success") is True
    assert project_a.add_project_summary("/project-a", "PROJECT_A_SUMMARY").get("success") is True
    assert project_a.add_project_instruction("PROJECT_A_INSTRUCTION").get("success") is True
    assert project_a.add_task_summary(
        {
            "task_id": "project-a-task",
            "task_type": "simple",
            "goal": "goal",
            "result": "completed",
            "summary": "PROJECT_A_TASK",
        }
    ).get("success") is True
    assert project_b.add_project_instruction("PROJECT_B_INSTRUCTION").get("success") is True
    assert other_user.add_project_instruction("OTHER_USER_INSTRUCTION").get("success") is True

    deleted = manager.delete_project_workspace("deletion-user", "project-a")
    assert deleted.get("success") is True and deleted.get("deleted") is True
    assert not project_a_context.workspace_dir.exists()
    cleanup = deleted.get("data", {})
    assert cleanup.get("project_summaries") == 1
    assert cleanup.get("project_instructions") == 1
    assert cleanup.get("task_history") == 1

    recreated = manager.get_context("deletion-user", "project-a")
    reloaded = _memory(recreated.database_path, recreated.user_id, recreated.project_id)
    rendered = reloaded.format_for_prompt()
    assert reloaded.get_project_memory()["projects"] == []
    assert reloaded.get_project_memory()["instructions"] == []
    assert reloaded.get_recent_tasks() == []
    assert "PROJECT_A_SUMMARY" not in rendered
    assert "PROJECT_A_INSTRUCTION" not in rendered
    assert "PROJECT_A_TASK" not in rendered
    assert "USER_SHARED" in rendered
    assert any(item["content"] == "USER_FACT" for item in reloaded.get_user_memory()["stable_facts"])
    assert "USER_FACT" not in rendered
    project_b.load_all()
    other_user.load_all()
    assert "PROJECT_B_INSTRUCTION" in project_b.format_for_prompt()
    assert "OTHER_USER_INSTRUCTION" in other_user.format_for_prompt()

    residue_context = manager.get_context("deletion-user", "missing-project")
    residue = _memory(database_path, "deletion-user", "missing-project")
    assert residue.add_project_instruction("DATABASE_RESIDUE").get("success") is True
    shutil.rmtree(residue_context.workspace_dir)
    absent = manager.delete_project_workspace("deletion-user", "missing-project")
    assert absent.get("success") is True and absent.get("deleted") is False
    assert absent.get("data", {}).get("project_instructions") == 1
    assert "DATABASE_RESIDUE" not in _memory(database_path, "deletion-user", "missing-project").format_for_prompt()

    guarded_context = manager.get_context("deletion-user", "guarded-project")
    with patch.object(
        PersistentMemory,
        "delete_project_scope",
        return_value={"success": False, "error": "forced database cleanup failure"},
    ):
        guarded = manager.delete_project_workspace("deletion-user", "guarded-project")
    assert guarded.get("success") is False
    assert guarded_context.workspace_dir.exists()


def test_scope_and_restart(database_path: Path) -> None:
    project_a = _memory(database_path, "user-a", "project-a")
    project_b = _memory(database_path, "user-a", "project-b")
    user_b = _memory(database_path, "user-b", "project-a")
    assert project_a.add_user_preference("shared", "USER_SHARED").get("success") is True
    assert project_a.add_stable_fact("USER_FACT", "Shared user fact").get("success") is True
    assert project_a.add_project_instruction("PROJECT_A_ONLY").get("success") is True
    assert project_b.add_project_instruction("PROJECT_B_ONLY").get("success") is True
    assert project_a.add_task_summary(
        {"task_id": "task-a", "task_type": "simple", "goal": "A", "result": "completed", "summary": "TASK_A"}
    ).get("success") is True
    assert project_b.add_task_summary(
        {"task_id": "task-b", "task_type": "simple", "goal": "B", "result": "completed", "summary": "TASK_B"}
    ).get("success") is True

    project_b.load_all()
    assert project_b.get_user_memory()["preferences"]["shared"]["value"] == "USER_SHARED"
    assert any(item["content"] == "USER_FACT" for item in project_b.get_user_memory()["stable_facts"])
    assert "PROJECT_A_ONLY" not in project_b.format_for_prompt()
    assert "PROJECT_B_ONLY" in project_b.format_for_prompt()
    assert [task["task_id"] for task in project_a.get_recent_tasks()] == ["task-a"]
    assert [task["task_id"] for task in project_b.get_recent_tasks()] == ["task-b"]
    assert user_b.get_counts() == {"user_memories": 0, "projects": 0, "project_instructions": 0, "tasks": 0}

    restarted = _memory(database_path, "user-a", "project-a")
    assert restarted.get_user_memory()["preferences"]["shared"]["value"] == "USER_SHARED"
    assert "PROJECT_A_ONLY" in restarted.format_for_prompt()
    assert restarted.get_recent_tasks()[0]["task_id"] == "task-a"


def _run_after_barrier(
    barrier: threading.Barrier,
    operation: Callable[[], dict[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    barrier.wait(timeout=10)
    results.append(operation())


def test_concurrent_mutation(database_path: Path) -> None:
    first = _memory(database_path, "concurrent-user", "project-a")
    second = _memory(database_path, "concurrent-user", "project-b")
    barrier = threading.Barrier(3)
    results: list[dict[str, Any]] = []
    threads = [
        threading.Thread(
            target=_run_after_barrier,
            args=(barrier, lambda: first.add_user_preference("a_only", "A_ONLY"), results),
        ),
        threading.Thread(
            target=_run_after_barrier,
            args=(barrier, lambda: second.add_user_preference("b_only", "B_ONLY"), results),
        ),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2 and all(result.get("success") is True for result in results)
    current = _memory(database_path, "concurrent-user", "project-a")
    preferences = current.get_user_memory()["preferences"]
    assert preferences["a_only"]["value"] == "A_ONLY"
    assert preferences["b_only"]["value"] == "B_ONLY"

    project_barrier = threading.Barrier(3)
    project_results: list[dict[str, Any]] = []
    project_threads = [
        threading.Thread(
            target=_run_after_barrier,
            args=(project_barrier, lambda: first.add_project_instruction("PROJECT_A_CONCURRENT"), project_results),
        ),
        threading.Thread(
            target=_run_after_barrier,
            args=(project_barrier, lambda: second.add_project_instruction("PROJECT_B_CONCURRENT"), project_results),
        ),
    ]
    for thread in project_threads:
        thread.start()
    project_barrier.wait(timeout=10)
    for thread in project_threads:
        thread.join(timeout=10)
    assert len(project_results) == 2 and all(result.get("success") is True for result in project_results)
    first.load_all()
    second.load_all()
    assert "PROJECT_A_CONCURRENT" in first.format_for_prompt()
    assert "PROJECT_B_CONCURRENT" not in first.format_for_prompt()
    assert "PROJECT_B_CONCURRENT" in second.format_for_prompt()
    assert "PROJECT_A_CONCURRENT" not in second.format_for_prompt()


def test_transaction_rollback(database_path: Path) -> None:
    database = DatabaseService(database_path)
    try:
        with database.write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_user_preference
                    (user_id, key, value, source, time_created, time_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("rollback-user", "temporary", "MUST_ROLL_BACK", "smoke", 1, 1),
            )
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass
    with database.read_transaction() as connection:
        row = connection.execute(
            "SELECT value FROM memory_user_preference WHERE user_id = ? AND key = ?",
            ("rollback-user", "temporary"),
        ).fetchone()
    assert row is None


def test_existing_api_contract(database_path: Path) -> None:
    memory = _memory(database_path, "api-user", "api-project")
    assert memory.add_user_preference("style", "short").get("success") is True
    created_at = memory.get_user_memory()["preferences"]["style"]["created_at"]
    assert memory.add_user_preference("style", "detailed").get("success") is True
    assert memory.get_user_memory()["preferences"]["style"]["created_at"] == created_at
    assert memory.add_stable_fact(
        "API_FACT",
        "API fact description",
        source_reference="source-a",
    ).get("success") is True
    assert memory.get_user_memory()["stable_facts"][0]["description"] == "API fact description"
    assert memory.add_stable_fact(
        "API_FACT",
        "Duplicate API fact description",
        source_reference="source-b",
    ).get("data", {}).get("updated") is True
    assert memory.get_user_memory()["stable_facts"][0]["description"] == "Duplicate API fact description"
    assert memory.add_project_summary("/api", "first", ["Python"], "active").get("success") is True
    assert memory.add_project_summary("/api", "second").get("success") is True
    project = memory.get_project_memory()["projects"][0]
    assert project["summary"] == "second" and project["tech_stack"] == ["Python"] and project["status"] == "active"
    assert memory.add_project_instruction("API_INSTRUCTION").get("success") is True
    assert memory.add_project_instruction("API_INSTRUCTION").get("data", {}).get("deduplicated") is True
    assert memory.add_task_summary(
        {
            "task_id": "api-task",
            "task_type": "simple",
            "goal": "goal",
            "result": "completed",
            "summary": "API_TASK",
            "modified_files": ["a.py"],
        }
    ).get("success") is True
    assert memory.add_task_summary(
        {"task_id": "api-task", "task_type": "simple", "goal": "duplicate", "result": "completed", "summary": "duplicate"}
    ).get("data", {}).get("deduplicated") is True
    assert memory.get_counts() == {"user_memories": 2, "projects": 1, "project_instructions": 1, "tasks": 1}
    search = memory.search_memory_references("API_FACT")
    references = search.get("data", {}).get("references", [])
    assert references
    assert memory.read_memory_reference(references[0]["reference_id"]).get("success") is True
    assert memory.delete_memory_reference(references[0]["reference_id"]).get("data", {}).get("deleted") == 1
    assert memory.clear_memory_type("project_instructions").get("data", {}).get("cleared") == 1

    trimmed = _memory(database_path, "trim-user", "trim-project")
    for index in range(102):
        assert trimmed.add_task_summary(
            {
                "task_id": f"trim-{index}",
                "task_type": "simple",
                "goal": f"goal-{index}",
                "result": "completed",
                "summary": f"summary-{index}",
            }
        ).get("success") is True
    tasks = trimmed.get_recent_tasks(limit=200)
    assert len(tasks) == 100
    assert tasks[0]["task_id"] == "trim-2" and tasks[-1]["task_id"] == "trim-101"
    for index in range(2):
        assert trimmed.add_task_summary(
            {
                "task_type": "simple",
                "goal": f"anonymous-{index}",
                "result": "completed",
                "summary": f"anonymous-{index}",
            }
        ).get("success") is True
    assert sum(not task["task_id"] for task in trimmed.get_recent_tasks(limit=200)) == 2


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        database_path = root / "horizon.db"
        test_database_foundation(database_path)
        test_database_service_lifecycle(database_path)
        test_reader_writer_coexistence(database_path)
        test_database_path_authority(root)
        test_scope_and_restart(database_path)
        test_concurrent_mutation(database_path)
        test_transaction_rollback(database_path)
        test_existing_api_contract(database_path)
        test_project_deletion_lifecycle(root, database_path)
    print("smoke_persistent_memory_sqlite_backend ok")


if __name__ == "__main__":
    main()
