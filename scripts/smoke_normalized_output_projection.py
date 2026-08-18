from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true", "TOOL_RESULT_EXTERNALIZE_CHARS": "1000"}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.final_observation_context import build_final_observation_context
from core.observation_compaction import build_tool_result_card
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_legacy_dict
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, tool: str, path: Path | None = None) -> ToolCallEnvelope:
    arguments = {"path": str(path)} if path else {}
    return ToolCallEnvelope(
        call_id=call_id, provider_call_id=call_id, source=ToolCallSource.STRUCTURED,
        raw_name=tool, tool_name=tool, canonical_name=tool, executable_name=tool,
        raw_arguments=json.dumps(arguments), parsed_arguments=arguments, sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE, metadata={"tool_spec_found": True, "task_id": "normalized-output"},
    )


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "projection"))
            path = root / "large.txt"
            body = "R" * 5000
            path.write_text(body, encoding="utf-8")
            observation = normalize_tool_result(
                _envelope("large-read", "read_file", path),
                {"success": True, "status": "success", "metadata": {"path": str(path)}, "data": body},
            )
            assert isinstance(observation.data["result"], str) and len(observation.data["result"]) < len(body)
            assert len(observation.output_text) <= 1250 and observation.output_text_chars == len(observation.output_text)
            assert observation.preview_chars == len(observation.output_text) and observation.source_chars == 5000
            legacy = observation_to_legacy_dict(observation)
            assert len(str(legacy["data"]["content"])) <= 1250
            rendered = json.dumps(legacy, ensure_ascii=False)
            assert "R" * 2000 not in rendered

            state = SimpleNamespace(metadata={"completion_observations": [legacy]})
            final_context = build_final_observation_context(state, SimpleNamespace(tool="read_file", policy_code="", metadata={}))
            assert "R" * 2000 not in json.dumps(final_context, ensure_ascii=False)

            small_path = root / "small.txt"
            small_path.write_text("small", encoding="utf-8")
            small = normalize_tool_result(_envelope("small", "read_file", small_path), {"success": True, "data": "small"})
            assert small.data["result"] == "small" and small.output_text == "small"

            for index, value in enumerate(({"items": ["D" * 200 for _ in range(20)]}, ["L" * 200 for _ in range(20)])):
                generic = normalize_tool_result(_envelope(f"generic-{index}", "generic_tool"), value)
                card = build_tool_result_card(generic, max_preview_chars=500)
                assert generic.content_ref and card.get("content_ref") == generic.content_ref
                assert len(json.dumps(card, ensure_ascii=False)) < len(json.dumps(value, ensure_ascii=False))
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_normalized_output_projection ok")


if __name__ == "__main__":
    main()
