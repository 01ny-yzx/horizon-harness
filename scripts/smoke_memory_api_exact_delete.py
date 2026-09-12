"""Verify API and frontend use only exact Memory deletion contracts."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.routes import memory as memory_routes
from api.schemas import (
    ClearMemoryTypeRequest,
    DeleteMemoryReferenceRequest,
    DeleteProjectInstructionRequest,
    DeleteUserPreferenceRequest,
)
from core.persistent_memory import PersistentMemory
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _reference(memory: PersistentMemory, query: str) -> str:
    result = memory.search_memory_references(query, limit=20)
    matches = result.get("data", {}).get("references", [])
    assert len(matches) == 1
    return str(matches[0]["reference_id"])


def test_import_and_static_contract() -> None:
    imported = subprocess.run(
        [sys.executable, "-c", "import api.app"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr

    production_files = [
        ROOT / "api" / "routes" / "memory.py",
        ROOT / "api" / "schemas.py",
        ROOT / "frontend" / "src" / "types" / "api.ts",
        ROOT / "frontend" / "src" / "api" / "client.ts",
        ROOT / "frontend" / "src" / "components" / "MemoryPanel.tsx",
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in production_files)
    for retired in (
        "forget_memory",
        "ForgetMemoryRequest",
        '"/memory/forget"',
        "forgetMemory",
    ):
        assert retired not in joined
    assert "keyword" not in (ROOT / "frontend" / "src" / "components" / "MemoryPanel.tsx").read_text(encoding="utf-8")

    routes = {route.path for route in memory_routes.router.routes}
    assert "/memory/forget" not in routes
    assert {
        "/memory/reference/delete",
        "/memory/preference/delete",
        "/memory/project-instruction/delete",
        "/memory/type/clear",
    }.issubset(routes)


def test_exact_api_mutations(root: Path) -> None:
    database_path = root / "horizon.db"
    manager = WorkspaceManager(root / "workspaces", database_path=database_path)

    def bind(user_id: str, project_id: str) -> None:
        set_current_workspace(manager.get_context(user_id, project_id))

    original = memory_routes.run_in_workspace
    memory_routes.run_in_workspace = bind
    try:
        bind("user-a", "project-a")
        memory = PersistentMemory(database_path=database_path, user_id="user-a", project_id="project-a")
        for content in ("EXACT_A shared", "EXACT_B shared", "EXACT_C shared"):
            assert memory.add_stable_fact(content, f"Description {content}")["success"]
        ref_b = _reference(memory, "EXACT_B")
        deleted = memory_routes.memory_reference_delete(
            DeleteMemoryReferenceRequest(
                user_id="user-a",
                project_id="project-a",
                reference_id=ref_b,
            )
        )
        assert deleted.success and deleted.data["deleted"] == 1
        memory.load_all()
        contents = {item["content"] for item in memory.get_user_memory()["stable_facts"]}
        assert contents == {"EXACT_A shared", "EXACT_C shared"}

        assert memory.add_user_preference("answer_style", "short")["success"]
        assert memory.add_user_preference("answer_tone", "calm")["success"]
        preference = memory_routes.memory_preference_delete(
            DeleteUserPreferenceRequest(
                user_id="user-a",
                project_id="project-a",
                key="answer_style",
            )
        )
        assert preference.success and preference.data["key"] == "answer_style"
        memory.load_all()
        assert set(memory.get_user_memory()["preferences"]) == {"answer_tone"}

        assert memory.add_project_instruction("DELETE_EXACT")["success"]
        assert memory.add_project_instruction("KEEP_EXACT")["success"]
        instruction = memory_routes.memory_project_instruction_delete(
            DeleteProjectInstructionRequest(
                user_id="user-a",
                project_id="project-a",
                content="DELETE_EXACT",
            )
        )
        assert instruction.success and instruction.data["deleted"] == 1
        memory.load_all()
        assert [item["content"] for item in memory.get_project_memory()["instructions"]] == ["KEEP_EXACT"]

        other = PersistentMemory(database_path=database_path, user_id="user-a", project_id="project-b")
        assert other.add_project_instruction("OTHER_PROJECT")["success"]
        cleared = memory_routes.memory_type_clear(
            ClearMemoryTypeRequest(
                user_id="user-a",
                project_id="project-a",
                memory_type="project_instructions",
            )
        )
        assert cleared.success and cleared.data["cleared"] == 1
        memory.load_all()
        other.load_all()
        assert memory.get_project_memory()["instructions"] == []
        assert [item["content"] for item in other.get_project_memory()["instructions"]] == ["OTHER_PROJECT"]
    finally:
        memory_routes.run_in_workspace = original


def main() -> None:
    test_import_and_static_contract()
    with TemporaryDirectory(prefix="horizon-memory-api-") as tmp:
        test_exact_api_mutations(Path(tmp))
    print("smoke_memory_api_exact_delete ok")


if __name__ == "__main__":
    main()
