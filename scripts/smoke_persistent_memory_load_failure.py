"""Focused smoke for persistent-memory database health boundaries."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.context_fusion import ContextFusionEngine, compact_memory_summary
from core.memory_reference_guidance import resolve_memory_reference_guidance
from core.persistent_memory import PersistentMemory
from tools import memory_tools


def test_database_read_failure_can_recover(root: Path) -> None:
    memory = PersistentMemory(
        database_path=root / "horizon.db",
        user_id="health-user",
        project_id="health-project",
    )
    assert memory.add_project_instruction("HEALTHY_PROJECT_MEMORY").get("success") is True

    @contextmanager
    def failed_read() -> Any:
        raise sqlite3.OperationalError("forced read failure")
        yield  # pragma: no cover

    with patch.object(memory.database, "read_transaction", failed_read):
        failed = memory.load_all()
        assert failed.get("success") is False
        assert memory._load_blocked is True
        assert failed.get("data", {}).get("load_errors")

        state = SimpleNamespace(
            task_profile=None,
            task_type="simple",
            rag_used=False,
            rag_enough_evidence=False,
            retrieved_chunks=[],
        )
        fused = ContextFusionEngine().build_fused_context("current question", state, memory)
        assert "HEALTHY_PROJECT_MEMORY" not in json.dumps(fused, ensure_ascii=False)
        assert ContextFusionEngine.memory_evidence(memory) == []
        assert compact_memory_summary(memory) == ""

        with patch("tools.memory_tools._memory", return_value=memory):
            reads = [
                memory_tools.search_memory_references("HEALTHY_PROJECT_MEMORY"),
                memory_tools.read_memory_reference("fact:anything"),
                memory_tools.list_memories("all"),
            ]
        assert all(result.get("success") is False for result in reads)
        assert memory.add_user_preference("blocked_user", "BLOCKED").get("success") is False
        assert memory.add_project_instruction("BLOCKED_PROJECT").get("success") is False

        reference_guidance = resolve_memory_reference_guidance(
            persistent_memory=memory,
        )
        assert reference_guidance.load_success is False
        assert reference_guidance.system_messages == ()

    recovered = memory.load_all()
    assert recovered.get("success") is True
    assert memory._load_blocked is False
    assert memory.add_project_instruction("RECOVERED_WRITE_OK").get("success") is True


def main() -> None:
    with TemporaryDirectory() as directory:
        test_database_read_failure_can_recover(Path(directory))
    print("smoke_persistent_memory_load_failure ok")


if __name__ == "__main__":
    main()
