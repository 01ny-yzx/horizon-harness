from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true", "TOOL_RESULT_EXTERNALIZE_CHARS": "12000"}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.final_observation_context import build_final_observation_context
from core.observation_compaction import build_tool_result_card
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_from_cache_snapshot, observation_to_cache_snapshot, observation_to_legacy_dict
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id, provider_call_id=call_id, source=ToolCallSource.STRUCTURED,
        raw_name="read_file", tool_name="read_file", canonical_name="read_file", executable_name="read_file",
        raw_arguments=json.dumps(arguments), parsed_arguments=arguments, sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE, metadata={"tool_spec_found": True, "task_id": "immutable-observation"},
    )


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "immutable"))
            path = root / "fact.txt"
            body = "UNIQUE_READ_RESULT"
            path.write_text(body, encoding="utf-8")
            observation = normalize_tool_result(
                _envelope("read-original", path),
                {"success": True, "status": "success", "metadata": {"path": str(path)}, "data": body},
            )
            snapshot = observation_to_cache_snapshot(observation)
            before = build_tool_result_card(observation)
            assert before["success"] and body in json.dumps(before, ensure_ascii=False)

            path.unlink()
            with patch("core.source_reference.resolve_source_file_metadata", side_effect=AssertionError("projection re-read source")):
                after_delete = build_tool_result_card(observation)
                legacy = observation_to_legacy_dict(observation)
                state = SimpleNamespace(metadata={"completion_observations": [legacy]})
                outcome = SimpleNamespace(tool="read_file", policy_code="", metadata={})
                final_context = build_final_observation_context(state, outcome)
            assert after_delete["success"] and body in json.dumps(after_delete, ensure_ascii=False)
            assert body in json.dumps(final_context, ensure_ascii=False)
            assert body in json.dumps(legacy, ensure_ascii=False)
            replay_missing = observation_from_cache_snapshot(_envelope("read-missing", path), snapshot)
            assert not replay_missing.success and replay_missing.error_code == "replay_source_missing"

            old = {
                "success": True, "status": "success", "tool": "read_file",
                "data": {"path": str(root / "old.txt"), "result": "OLD_FACT"},
                "metadata": {"path": str(root / "old.txt")},
            }
            old_card = build_tool_result_card(old)
            assert not old_card.get("source_ref")

            path.write_text(body, encoding="utf-8")
            stable = normalize_tool_result(
                _envelope("read-stable", path),
                {"success": True, "status": "success", "data": {"path": str(path), "content": body}},
            )
            stable_snapshot = observation_to_cache_snapshot(stable)
            stable_card = build_tool_result_card(stable)
            path.write_text("CHANGED", encoding="utf-8")
            assert build_tool_result_card(stable) == stable_card
            replay_changed = observation_from_cache_snapshot(_envelope("read-changed", path), stable_snapshot)
            assert not replay_changed.success and replay_changed.error_code == "replay_source_hash_mismatch"
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_immutable_observation_projection ok")


if __name__ == "__main__":
    main()
