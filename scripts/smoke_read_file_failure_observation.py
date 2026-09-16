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

_ENV = {
    "AGENT_ACCESS_MODE": "full_access",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "TOOL_RESULT_EXTERNALIZE_CHARS": "12000",
}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.final_observation_context import build_final_observation_context
from core.loop import _path_grounding_from_observation
from core.observation_compaction import build_tool_result_card
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import (
    _partition_legacy_read_metadata,
    normalize_tool_result,
    observation_to_legacy_dict,
)
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.file_tools import read_file as tool_read_file


FORBIDDEN = {
    "content", "text", "body", "markdown", "preview", "tables", "rows", "data", "values",
    "source_ref", "content_ref", "source_sha256", "content_sha256", "source_chars", "content_chars",
    "source_bytes", "content_bytes",
}


def _envelope(call_id: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name="read_file",
        tool_name="read_file",
        canonical_name="read_file",
        executable_name="read_file",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "read-file-failure"},
    )


def _final_context(observation: object) -> list[dict[str, object]]:
    legacy = observation_to_legacy_dict(observation)  # type: ignore[arg-type]
    state = SimpleNamespace(metadata={"completion_observations": [legacy]})
    return build_final_observation_context(state, SimpleNamespace(tool="read_file", policy_code="", metadata={}))


def _assert_no_business_body(value: object, *, allow_grounding: bool = False) -> None:
    if isinstance(value, dict):
        assert FORBIDDEN.isdisjoint(value)
        if not allow_grounding:
            assert "path_grounding" not in value
        for nested in value.values():
            _assert_no_business_body(nested, allow_grounding=allow_grounding)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_business_body(nested, allow_grounding=allow_grounding)


def _assert_failure(raw: dict[str, object], path: Path, call_id: str) -> object:
    assert raw["success"] is False
    data = raw.get("data")
    metadata = raw.get("metadata")
    assert isinstance(data, dict) and "path_grounding" not in data
    assert isinstance(metadata, dict) and metadata.get("path")
    assert isinstance(metadata.get("path_grounding"), dict)
    observation = normalize_tool_result(_envelope(call_id, path), raw)
    assert observation.success is False
    assert observation.error and observation.error_code
    assert observation.data["requested_path"]
    assert observation.metadata.get("path")
    assert isinstance(observation.metadata.get("path_grounding"), dict)
    assert not observation.source_ref and not observation.content_ref
    _assert_no_business_body(observation.data)
    legacy = observation_to_legacy_dict(observation)
    card = build_tool_result_card(observation)
    final_context = _final_context(observation)
    _assert_no_business_body(legacy["data"])
    _assert_no_business_body(card, allow_grounding=True)
    _assert_no_business_body(final_context, allow_grounding=True)
    return observation


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chdir(root)
            workspace = WorkspaceManager(root / "workspace").get_context("smoke", "read-file-failure")
            set_current_workspace(workspace)
            artifact_dir = workspace.workspace_dir / "tool_results"

            missing = root / "missing.txt"
            missing_raw = tool_read_file(str(missing))
            missing_observation = _assert_failure(missing_raw, missing, "missing")
            assert missing_observation.error_code == "file_not_found"
            missing_summary = _path_grounding_from_observation(missing_observation)
            assert missing_summary["tool_name"] == "read_file"
            assert missing_summary["resolved_path"] == str(missing.resolve())

            directory_path = root / "directory"
            directory_path.mkdir()
            directory_raw = tool_read_file(str(directory_path))
            directory_observation = _assert_failure(directory_raw, directory_path, "directory")
            assert directory_observation.error_code == "path_is_not_file"
            assert "不是文件" in directory_observation.error
            assert _path_grounding_from_observation(directory_observation)["resolved_path"] == str(directory_path)

            binary = root / "binary.txt"
            binary.write_bytes(b"\xff\xfe\xfa")
            binary_raw = tool_read_file(str(binary))
            binary_observation = _assert_failure(binary_raw, binary, "binary")
            assert binary_observation.error == "文件不是 UTF-8 文本，无法读取。"

            grounding = missing_raw["metadata"]["path_grounding"]
            flat = normalize_tool_result(
                _envelope("legacy-flat", missing),
                {
                    "success": False,
                    "error": "文件不存在",
                    "data": {"requested_path": str(missing), "path": str(missing), "path_grounding": grounding},
                },
            )
            assert flat.metadata["path_grounding"] == grounding
            _assert_no_business_body(flat.data)

            nested = normalize_tool_result(
                _envelope("legacy-nested", missing),
                {
                    "success": False,
                    "error": "文件不存在",
                    "data": {
                        "result": {
                            "requested_path": str(missing),
                            "path": str(missing),
                            "path_grounding": grounding,
                        }
                    },
                },
            )
            assert nested.metadata["path_grounding"] == grounding
            _assert_no_business_body(nested.data)

            grounding_a = {"marker": "A", "resolved_path": str(missing)}
            priority = normalize_tool_result(
                _envelope("priority", missing),
                {
                    "success": False,
                    "error": "文件不存在",
                    "metadata": {"path_grounding": grounding_a},
                    "data": {
                        "path_grounding": {"marker": "B"},
                        "result": {"path_grounding": {"marker": "C"}, "path": str(missing)},
                    },
                },
            )
            assert priority.metadata["path_grounding"] == grounding_a
            _assert_no_business_body(priority.data)

            helper_payload = {"metadata": {"path_grounding": grounding_a}}
            helper_data = {
                "path_grounding": {"marker": "B"},
                "result": {"path_grounding": {"marker": "C"}, "resolved_path": str(missing)},
            }
            _partition_legacy_read_metadata("read_file", helper_payload, helper_data, argument_path=str(missing))
            once = json.dumps({"payload": helper_payload, "data": helper_data}, ensure_ascii=False, sort_keys=True)
            _partition_legacy_read_metadata("read_file", helper_payload, helper_data, argument_path=str(missing))
            twice = json.dumps({"payload": helper_payload, "data": helper_data}, ensure_ascii=False, sort_keys=True)
            assert once == twice

            text_file = root / "ok.txt"
            text_file.write_text("read-file-success", encoding="utf-8")
            success_raw = tool_read_file(str(text_file))
            success_page = success_raw["data"]
            assert success_raw["success"] is True and isinstance(success_page, dict)
            assert success_page["content"].startswith("1: read-file-success")
            assert success_page["line_start"] == 1 and success_page["line_end"] == 1
            assert success_page["truncated"] is False and success_page["next_offset"] is None
            assert success_raw["metadata"]["path"] == str(text_file)
            assert isinstance(success_raw["metadata"]["path_grounding"], dict)
            success = normalize_tool_result(_envelope("success", text_file), success_raw)
            assert success.success is True and success.output_text == success_page["content"]
            assert not success.source_ref
            assert not success.content_ref
            assert not list(artifact_dir.glob("*"))
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_read_file_failure_observation ok")


if __name__ == "__main__":
    main()
