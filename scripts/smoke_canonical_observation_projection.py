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

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true", "TOOL_RESULT_EXTERNALIZE_CHARS": "12000"}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.context_budget import apply_context_budget
from core.final_observation_context import build_final_observation_context
from core.observation_compaction import build_tool_result_card
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import MAX_MODEL_TEXT_CHARS, normalize_tool_result, observation_to_legacy_dict
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, tool: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id, provider_call_id=call_id, source=ToolCallSource.STRUCTURED,
        raw_name=tool, tool_name=tool, canonical_name=tool, executable_name=tool,
        raw_arguments=json.dumps(arguments), parsed_arguments=arguments, sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE, metadata={"tool_spec_found": True, "task_id": "canonical-projection"},
    )


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "canonical"))
            tail = "UNIQUE_TAIL_MARKER_SHOULD_NOT_BE_MODEL_VISIBLE"
            body = "A" * (5000 - len(tail)) + tail
            source = root / "large.txt"
            source.write_text(body, encoding="utf-8")
            observation = normalize_tool_result(
                _envelope("large-read", "read_file", source),
                {"success": True, "status": "success", "metadata": {"path": str(source)}, "data": body},
            )
            canonical = observation.data["result"]
            assert observation.source_chars == 5000 and observation.source_ref == str(source.resolve())
            assert not observation.content_ref and len(canonical) <= MAX_MODEL_TEXT_CHARS
            assert len(observation.output_text) <= MAX_MODEL_TEXT_CHARS and tail not in observation.output_text
            assert observation.data["content"] == canonical == observation.data["text"]

            legacy = observation_to_legacy_dict(observation)
            for key in ("result", "content", "text"):
                assert legacy["data"][key] == canonical and len(legacy["data"][key]) <= MAX_MODEL_TEXT_CHARS
            assert tail not in json.dumps(legacy, ensure_ascii=False)

            card = build_tool_result_card(observation)
            assert tail not in json.dumps(card, ensure_ascii=False)
            state = SimpleNamespace(metadata={"completion_observations": [legacy]})
            final_context = build_final_observation_context(state, SimpleNamespace(tool="read_file", policy_code="", metadata={}))
            assert tail not in json.dumps(final_context, ensure_ascii=False)
            history, _ = apply_context_budget(
                [{"role": "user", "content": "read"}, {"role": "tool", "name": "read_file", "tool_call_id": "large-read", "content": json.dumps(legacy, ensure_ascii=False)}],
                tools=[], runtime_lane="build", task_state=SimpleNamespace(metadata={}),
                model_context_tokens=8192, requested_output_tokens=1024,
            )
            assert tail not in json.dumps(history, ensure_ascii=False)

            small_source = root / "small.txt"
            small_body = "small complete result"
            small_source.write_text(small_body, encoding="utf-8")
            small = normalize_tool_result(_envelope("small-read", "read_file", small_source), {"success": True, "data": small_body})
            assert small.data["result"] == small_body and small.output_text == small_body and not small.compacted

            document = root / "document.pdf"
            document.write_bytes(b"%PDF-1.7\x00document")
            table_obs = normalize_tool_result(
                _envelope("document-table", "read_document", document),
                {"success": True, "data": {"path": str(document), "text": "small table", "tables": [{"name": "T", "rows": [["A", "B"], ["C", "D"]]}]}},
            )
            table = table_obs.data["result"]["tables"][0]
            assert table["preview_rows"] == [["A", "B"], ["C", "D"]]
            assert not any(key in table for key in ("rows", "data", "values"))
            assert not table_obs.content_ref and not table_obs.compacted

            derived_tail = "DERIVED_DOCUMENT_TAIL_NOT_VISIBLE"
            derived = "D" * 5000 + derived_tail
            document_obs = normalize_tool_result(
                _envelope("document", "read_document", document),
                {"success": True, "data": {"path": str(document), "text": derived}},
            )
            assert document_obs.content_ref and document_obs.source_ref == str(document.resolve())
            assert derived_tail not in json.dumps(document_obs.data, ensure_ascii=False)
            artifact = json.loads(Path(document_obs.content_ref).read_text(encoding="utf-8"))
            assert artifact["result"]["text"].endswith(derived_tail)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_canonical_observation_projection ok")


if __name__ == "__main__":
    main()
