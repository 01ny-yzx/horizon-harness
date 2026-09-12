"""Focused smoke for removal of the long-term-memory JSON file backend."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import persistent_memory as persistent_memory_module
from core import workspace_runtime
from core.persistent_memory import PersistentMemory
from core.workspace import WorkspaceContext, WorkspaceManager
from core.workspace_runtime import set_current_workspace
from scripts.reset_memory import apply_plan, build_plan
from tools.workspace_tools import get_workspace_status


REMOVED_PERSISTENT_MEMORY_SYMBOLS = {
    "USER_MEMORY_FILE",
    "PROJECT_MEMORY_FILE",
    "TASK_HISTORY_FILE",
    "MEMORY_INDEX_FILE",
    "_locked_mutation",
}


def test_removed_production_symbols() -> None:
    for symbol in REMOVED_PERSISTENT_MEMORY_SYMBOLS:
        assert not hasattr(persistent_memory_module, symbol), symbol
    for method in ("_mutation_guard", "_ensure_store", "_read_json", "_write_json"):
        assert not hasattr(PersistentMemory, method), method
    source = inspect.getsource(persistent_memory_module)
    for residue in (".memory.lock", "fcntl", "tempfile.mkstemp", "os.replace", "os.fsync"):
        assert residue not in source, residue


def test_workspace_has_no_memory_directories(root: Path) -> None:
    manager = WorkspaceManager(root / "workspaces", database_path=root / "horizon.db")
    context = manager.get_context("scope-user", "scope-project")
    assert "user_memory_dir" not in WorkspaceContext.__dataclass_fields__
    assert "memory_dir" not in WorkspaceContext.__dataclass_fields__
    assert not (manager.root_dir / "scope-user" / "_user_memory").exists()
    assert not (context.workspace_dir / "memory_store").exists()
    assert not hasattr(workspace_runtime, "get_user_memory_dir")
    assert not hasattr(workspace_runtime, "get_memory_dir")

    set_current_workspace(context)
    status = get_workspace_status()
    assert status.get("success") is True
    data = status.get("data", {})
    assert "user_memory_dir_exists" not in data
    assert "memory_dir_exists" not in data


def test_repository_placeholder_removed() -> None:
    placeholder = ROOT / "memory_store" / ".gitkeep"
    assert not placeholder.exists()
    gitignore_lines = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert "!memory_store/" not in gitignore_lines
    assert "!memory_store/.gitkeep" not in gitignore_lines


def test_sqlite_reset(root: Path) -> None:
    missing_database = root / "missing-horizon.db"
    missing_plan = build_plan(root, database_path=missing_database)
    assert sum(missing_plan.memory_counts.values()) == 0
    assert not missing_database.exists()

    database_path = root / "horizon.db"
    memory = PersistentMemory(database_path=database_path, user_id="reset-user", project_id="reset-project")
    assert memory.add_user_preference("style", "short").get("success") is True
    assert memory.add_stable_fact("RESET_FACT", "Reset fact fixture").get("success") is True
    assert memory.add_project_summary("/reset", "RESET_SUMMARY").get("success") is True
    assert memory.add_project_instruction("RESET_INSTRUCTION").get("success") is True
    assert memory.add_task_summary(
        {
            "task_id": "reset-task",
            "task_type": "simple",
            "goal": "reset",
            "result": "completed",
            "summary": "RESET_TASK",
        }
    ).get("success") is True

    dry_run = build_plan(root, database_path=database_path)
    assert sum(dry_run.memory_counts.values()) == 5
    assert PersistentMemory.count_all_memory(database_path).get("data", {}).get("total") == 5

    with memory.database.write_transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER smoke_block_memory_reset
            BEFORE DELETE ON memory_stable_fact
            BEGIN
                SELECT RAISE(ABORT, 'forced reset rollback');
            END
            """
        )
    failed_reset = PersistentMemory.reset_all_memory(database_path)
    assert failed_reset.get("success") is False
    assert PersistentMemory.count_all_memory(database_path).get("data", {}).get("total") == 5
    with memory.database.write_transaction() as connection:
        connection.execute("DROP TRIGGER smoke_block_memory_reset")

    reset = apply_plan(dry_run)
    assert reset.get("success") is True
    assert reset.get("data", {}).get("deleted") == 5
    assert database_path.exists()
    assert PersistentMemory.count_all_memory(database_path).get("data", {}).get("total") == 0

    restarted = PersistentMemory(database_path=database_path, user_id="reset-user", project_id="reset-project")
    assert restarted.get_counts() == {
        "user_memories": 0,
        "projects": 0,
        "project_instructions": 0,
        "tasks": 0,
    }
    assert restarted.add_project_instruction("AFTER_RESET").get("success") is True
    assert "AFTER_RESET" in PersistentMemory(
        database_path=database_path,
        user_id="reset-user",
        project_id="reset-project",
    ).format_for_prompt()


def main() -> None:
    test_removed_production_symbols()
    test_repository_placeholder_removed()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        test_workspace_has_no_memory_directories(root)
        test_sqlite_reset(root)
    print("smoke_persistent_memory_json_backend_removal ok")


if __name__ == "__main__":
    main()
