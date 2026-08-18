"""Prompt contract for unambiguous local-file locations."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.prompt_pack import build_initial_agent_turn_pack, build_tool_call_pack


def _text(pack) -> str:
    return "\n".join(str(message.get("content") or "") for message in pack.messages)


def main() -> None:
    schema = {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}
    initial = build_initial_agent_turn_pack(user_input="读取文件", tools=[schema], access_mode="read_only")
    continuation = build_tool_call_pack(
        user_input="读取文件", task_state=SimpleNamespace(metadata={}),
        tools=[schema], memory_messages=[],
    )
    source = _text(initial) + "\n" + _text(continuation)
    lowered = source.lower()
    for phrase in ("explicit absolute path", "project-relative path", "ask when an existing file location is unknown"):
        assert phrase in lowered, phrase
    assert "scan the computer" not in lowered
    assert "file extension" not in lowered
    print("smoke_ambiguous_local_file_location_contract: PASS")


if __name__ == "__main__":
    main()
