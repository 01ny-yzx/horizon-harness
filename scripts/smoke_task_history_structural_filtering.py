"""Verify task-history Prompt visibility uses only structured metadata."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.context_fusion import ContextFusionEngine
from core.loop import AgentLoop
from core.persistent_memory import PersistentMemory


def _memory(tasks: list[dict[str, Any]]) -> PersistentMemory:
    memory = object.__new__(PersistentMemory)
    memory.user_memory = {"preferences": {}, "stable_facts": []}
    memory.project_memory = {"projects": []}
    memory.task_history = {"tasks": tasks}
    memory.memory_index = {}
    return memory


def _task(
    goal: str,
    *,
    summary: str = "completed",
    hidden: bool = False,
) -> dict[str, Any]:
    task = {
        "task_id": goal,
        "task_type": "simple",
        "goal": goal,
        "summary": summary,
        "result": "completed",
    }
    if hidden:
        task["hidden_from_memory_prompt"] = True
    return task


class _FakePersistentMemory:
    def __init__(self) -> None:
        self.summaries: list[dict[str, Any]] = []

    def add_task_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        self.summaries.append(summary)
        return {"success": True}


def _history_state(*, attempted: bool | None) -> SimpleNamespace:
    metadata: dict[str, Any] = {}
    if attempted is not None:
        metadata["memory_tool_attempted"] = attempted
    return SimpleNamespace(
        metadata=metadata,
        memory_saved=False,
        saved_memory_types=[],
    )


def _history_loop(memory: _FakePersistentMemory) -> AgentLoop:
    loop = object.__new__(AgentLoop)
    loop.persistent_memory = memory
    loop._build_task_summary = lambda _state, answer: {
        "goal": "ordinary task",
        "summary": answer,
    }
    return loop


def _visible_goals(memory: PersistentMemory) -> list[str]:
    return [str(task.get("goal") or "") for task in memory.get_prompt_safe_recent_tasks(20)]


def main() -> None:
    ordinary = [
        _task("删除 users 表中的测试数据"),
        _task("记住这个临时文件名，然后继续处理"),
        _task("给用户偏好表增加索引"),
        _task("调用 fetch_url 后保存结果", summary="web_search search result"),
    ]
    memory = _memory(ordinary)
    assert _visible_goals(memory) == [task["goal"] for task in ordinary]

    for task in ordinary:
        rag_prompt = _memory([task]).format_for_prompt(
            mode="rag",
            include_task_history=True,
        )
        assert "Recent tasks:" in rag_prompt
        assert "- [simple]" in rag_prompt
        assert "(completed)" in rag_prompt

    hidden = _task("删除但显式隐藏", hidden=True)
    visible = _task("普通任务")
    hidden_memory = _memory([hidden, visible])
    assert _visible_goals(hidden_memory) == ["普通任务"]
    assert hidden_memory._clean_task_summary({
        "goal": "x",
        "hidden_from_memory_prompt": True,
    })["hidden_from_memory_prompt"] is True

    disabled_prompt = memory.format_for_prompt(
        mode="rag",
        include_task_history=False,
    )
    assert "Recent tasks:" not in disabled_prompt
    for task in ordinary:
        assert task["goal"] not in disabled_prompt

    plain = _task("普通任务")
    keyword_heavy = _task("删除 记住 偏好 fetch_url web_search search result")
    assert _visible_goals(_memory([plain, keyword_heavy])) == [
        plain["goal"],
        keyword_heavy["goal"],
    ]

    for attempted, expected_count in ((True, 0), (False, 1), (None, 1)):
        fake_memory = _FakePersistentMemory()
        state = _history_state(attempted=attempted)
        _history_loop(fake_memory)._save_task_history_after_task(state, "done")
        assert len(fake_memory.summaries) == expected_count

    fusion_memory = _memory([
        _task("删除 users 表测试数据", summary="history-delete"),
        _task("记住临时文件名然后继续处理", summary="history-remember"),
        _task("调用 fetch_url 后保存结果", summary="history-fetch"),
    ])
    fused = ContextFusionEngine().build_fused_context(
        "continue",
        SimpleNamespace(),
        fusion_memory,
    )
    recent_tasks = fused["data"]["sections"]["recent_tasks"]
    assert "weak background only" in recent_tasks
    for summary in ("history-delete", "history-remember", "history-fetch"):
        assert summary in recent_tasks

    print("smoke_task_history_structural_filtering ok")


if __name__ == "__main__":
    main()
