"""Focused smoke for bounded, model-visible read_file pagination."""

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
from core.prompt_pack import single_file_read_observation_payload
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import (
    normalize_tool_result,
    observation_to_legacy_dict,
    observation_to_model_message_json,
)
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.file_tools import FILE_TOOL_SCHEMAS, read_file


def _page(result: dict) -> dict:
    assert result["success"] is True, result
    page = result.get("data")
    assert isinstance(page, dict), page
    return page


def _envelope(path: Path, call_id: str = "read-page") -> ToolCallEnvelope:
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
        metadata={"tool_spec_found": True, "task_id": "read-pagination"},
    )


def _final_context_entry(path: Path, raw: dict, *, call_id: str) -> dict:
    observation = normalize_tool_result(_envelope(path, call_id), raw)
    legacy = observation_to_legacy_dict(observation)
    state = SimpleNamespace(metadata={"completion_observations": [legacy]})
    context = build_final_observation_context(
        state,
        SimpleNamespace(tool="read_file", policy_code="", metadata={}),
    )
    assert len(context) == 1
    return context[0]


def test_small_file(root: Path) -> None:
    target = root / "small.txt"
    target.write_text("alpha\nbeta", encoding="utf-8")
    page = _page(read_file(str(target)))
    assert page["content"].startswith("1: alpha\n2: beta")
    assert "End of file - total 2 lines" in page["content"]
    assert page["line_start"] == 1 and page["line_end"] == 2
    assert page["truncated"] is False and page["next_offset"] is None


def test_limit_and_real_line_numbers(root: Path) -> None:
    target = root / "paged.txt"
    target.write_text("one\ntwo\nthree\nfour", encoding="utf-8")
    first = _page(read_file(str(target), limit=2))
    assert first["content"].startswith("1: one\n2: two")
    assert first["truncated"] is True and first["next_offset"] == 3
    assert "Use offset=3 to continue" in first["content"]
    second = _page(read_file(str(target), offset=3, limit=2))
    assert second["content"].startswith("3: three\n4: four")
    assert second["truncated"] is False and second["next_offset"] is None


def test_default_line_limit(root: Path) -> None:
    target = root / "many-lines.txt"
    target.write_text("\n".join(f"line-{index}" for index in range(1, 2002)), encoding="utf-8")
    page = _page(read_file(str(target)))
    assert page["line_start"] == 1 and page["line_end"] == 2000
    assert page["truncated"] is True and page["next_offset"] == 2001
    assert "2000: line-2000" in page["content"]
    assert "2001: line-2001" not in page["content"]


def test_utf8_byte_cap(root: Path) -> None:
    target = root / "byte-cap.txt"
    target.write_text("\n".join("界" * 300 for _ in range(60)), encoding="utf-8")
    raw = read_file(str(target))
    page = _page(raw)
    assert page["line_end"] < 60
    assert page["truncated"] is True
    assert page["next_offset"] == page["line_end"] + 1
    assert 0 < page["page_bytes"] <= 50 * 1024
    assert "Output capped at 50 KB" in page["content"]
    final_entry = _final_context_entry(target, raw, call_id="read-byte-cap")
    assert final_entry["truncated"] is True


def test_long_line_is_locally_truncated(root: Path) -> None:
    target = root / "long-line.txt"
    target.write_text(("x" * 2500) + "\nafter", encoding="utf-8")
    page = _page(read_file(str(target)))
    assert "... (line truncated to 2000 chars)" in page["content"]
    assert "2: after" in page["content"]
    assert page["truncated"] is False and page["next_offset"] is None


def test_offset_and_parameter_boundaries(root: Path) -> None:
    target = root / "two-lines.txt"
    target.write_text("one\ntwo", encoding="utf-8")
    out_of_range = read_file(str(target), offset=3)
    assert out_of_range["success"] is False
    assert out_of_range.get("error_code") == "offset_out_of_range"
    for arguments in ({"offset": 0}, {"limit": 0}, {"limit": 2001}):
        invalid = read_file(str(target), **arguments)
        assert invalid["success"] is False
        assert invalid.get("error_code") == "invalid_read_pagination"

    empty = root / "empty.txt"
    empty.write_text("", encoding="utf-8")
    page = _page(read_file(str(empty)))
    assert page["line_start"] == 1 and page["line_end"] == 0
    assert page["truncated"] is False and page["next_offset"] is None
    assert "End of file - total 0 lines" in page["content"]


def test_bounded_page_is_not_compacted_again(root: Path) -> None:
    marker = "TAIL_MARKER_AFTER_12000_CHARS"
    target = root / "model-visible.txt"
    target.write_text(("z" * 180 + "\n") * 80 + marker, encoding="utf-8")
    raw = read_file(str(target))
    page = _page(raw)
    assert len(page["content"]) > 12_000 and marker in page["content"]

    observation = normalize_tool_result(_envelope(target), raw)
    model_json = observation_to_model_message_json(observation, runtime_lane="build")
    assert marker in model_json
    legacy = observation_to_legacy_dict(observation)
    assert marker in single_file_read_observation_payload(legacy)["file_content"]
    state = SimpleNamespace(metadata={"completion_observations": [legacy]})
    final_context = build_final_observation_context(
        state,
        SimpleNamespace(tool="read_file", policy_code="", metadata={}),
    )
    assert marker in json.dumps(final_context, ensure_ascii=False)
    assert final_context[0]["truncated"] is False


def test_schema_exposes_pagination() -> None:
    schema = next(
        item["function"]
        for item in FILE_TOOL_SCHEMAS
        if item.get("function", {}).get("name") == "read_file"
    )
    parameters = schema["parameters"]
    assert parameters["required"] == ["path"]
    assert set(parameters["properties"]) == {"path", "offset", "limit"}
    assert parameters["properties"]["offset"]["default"] == 1
    assert parameters["properties"]["offset"]["minimum"] == 1
    assert parameters["properties"]["limit"]["default"] == 2000
    assert parameters["properties"]["limit"]["minimum"] == 1
    assert parameters["properties"]["limit"]["maximum"] == 2000


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            workspace = WorkspaceManager(root / "workspace").get_context("smoke", "read-pagination")
            set_current_workspace(workspace)
            files = workspace.workspace_dir
            test_small_file(files)
            test_limit_and_real_line_numbers(files)
            test_default_line_limit(files)
            test_utf8_byte_cap(files)
            test_long_line_is_locally_truncated(files)
            test_offset_and_parameter_boundaries(files)
            test_bounded_page_is_not_compacted_again(files)
            test_schema_exposes_pagination()
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_read_file_pagination ok")


if __name__ == "__main__":
    main()
