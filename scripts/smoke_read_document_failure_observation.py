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
    _partition_legacy_read_document_metadata,
    is_recoverable_observation,
    normalize_tool_result,
    observation_to_legacy_dict,
)
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.file_tools import read_document as tool_read_document


FORBIDDEN_BODY_KEYS = {
    "content", "text", "body", "markdown", "preview", "tables", "rows", "values",
    "source_ref", "content_ref", "source_sha256", "content_sha256", "source_chars", "content_chars",
    "source_bytes", "content_bytes",
}


def _envelope(call_id: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name="read_document",
        tool_name="read_document",
        canonical_name="read_document",
        executable_name="read_document",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "document-failure"},
    )


def _final_context(observation: object) -> list[dict[str, object]]:
    legacy = observation_to_legacy_dict(observation)  # type: ignore[arg-type]
    state = SimpleNamespace(metadata={"completion_observations": [legacy]})
    return build_final_observation_context(state, SimpleNamespace(tool="read_document", policy_code="", metadata={}))


def _assert_no_body_fields(value: object, *, allow_path_grounding: bool = False) -> None:
    if isinstance(value, dict):
        assert FORBIDDEN_BODY_KEYS.isdisjoint(value)
        if not allow_path_grounding:
            assert "path_grounding" not in value
        for nested in value.values():
            _assert_no_body_fields(nested, allow_path_grounding=allow_path_grounding)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_body_fields(nested, allow_path_grounding=allow_path_grounding)


def _assert_failure(observation: object, *, expected_code: str, expected_path: Path) -> None:
    assert observation.success is False  # type: ignore[attr-defined]
    assert observation.status in {"failed", "error", "blocked", "denied", "rejected", "cancelled"}  # type: ignore[attr-defined]
    assert observation.error  # type: ignore[attr-defined]
    assert observation.error_code == expected_code  # type: ignore[attr-defined]
    assert observation.data["requested_path"] == str(expected_path)  # type: ignore[attr-defined]
    assert observation.data["error_code"] == expected_code  # type: ignore[attr-defined]
    assert observation.data["result"]["error_code"] == expected_code  # type: ignore[attr-defined]
    assert not observation.source_ref and not observation.content_ref  # type: ignore[attr-defined]
    _assert_no_body_fields(observation.data)  # type: ignore[attr-defined]


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            workspace = WorkspaceManager(root / "workspace").get_context("smoke", "document-failure")
            set_current_workspace(workspace)
            artifact_dir = workspace.workspace_dir / "tool_results"

            corrupt = root / "corrupt.xlsx"
            corrupt.write_bytes(b"not-a-valid-xlsx")
            raw = tool_read_document(str(corrupt))
            assert raw["success"] is False
            assert raw["metadata"]["path"] == str(corrupt.resolve())
            assert isinstance(raw["metadata"]["path_grounding"], dict)
            assert isinstance(raw["data"]["error"], dict)
            assert raw["data"]["error"]["code"] == "document_xlsx_invalid_or_corrupt"
            observation = normalize_tool_result(_envelope("corrupt", corrupt), raw)
            assert not is_recoverable_observation(observation)
            _assert_failure(
                observation,
                expected_code="document_xlsx_invalid_or_corrupt",
                expected_path=corrupt,
            )
            assert observation.metadata["path"] == str(corrupt.resolve())
            assert isinstance(observation.metadata["path_grounding"], dict)
            grounding_summary = _path_grounding_from_observation(observation)
            assert grounding_summary["resolved_path"] == str(corrupt.resolve())
            assert "path_grounding" not in observation.data and "path_grounding" not in observation.data["result"]
            assert not list(artifact_dir.glob("document_result_*.json"))

            legacy = observation_to_legacy_dict(observation)
            card = build_tool_result_card(observation)
            final_context = _final_context(observation)
            _assert_no_body_fields(legacy["data"])
            _assert_no_body_fields(card, allow_path_grounding=True)
            for rendered in (
                json.dumps(legacy, ensure_ascii=False),
                json.dumps(card, ensure_ascii=False),
                json.dumps(final_context, ensure_ascii=False),
            ):
                assert "document_xlsx_invalid_or_corrupt" in rendered
                assert observation.error in rendered
            assert card["path_grounding"]["resolved_path"] == str(corrupt.resolve())
            final_item = final_context[0]
            assert final_item["error_code"] == "document_xlsx_invalid_or_corrupt"
            assert final_item["data_summary"]["requested_path"] == str(corrupt)
            assert final_item["data_summary"]["path_grounding"]["resolved_path"] == str(corrupt.resolve())

            missing = root / "missing.xlsx"
            missing_raw = tool_read_document(str(missing))
            missing_observation = normalize_tool_result(_envelope("missing", missing), missing_raw)
            expected_missing_code = str(missing_raw["data"]["error"]["code"])
            _assert_failure(missing_observation, expected_code=expected_missing_code, expected_path=missing)
            assert isinstance(missing_observation.metadata.get("path_grounding"), dict)

            directory_path = root / "document-directory"
            directory_path.mkdir()
            directory_raw = tool_read_document(str(directory_path))
            directory_observation = normalize_tool_result(
                _envelope("directory", directory_path), directory_raw
            )
            expected_directory_code = str(directory_raw["data"]["error"]["code"])
            assert expected_directory_code == "document_path_is_directory"
            _assert_failure(
                directory_observation,
                expected_code=expected_directory_code,
                expected_path=directory_path,
            )
            assert "document_sensitive_file_blocked" not in json.dumps(
                directory_raw, ensure_ascii=False
            )

            unsupported = root / "unsupported.rtf"
            unsupported.write_text("unsupported", encoding="utf-8")
            unsupported_raw = tool_read_document(str(unsupported))
            unsupported_observation = normalize_tool_result(_envelope("unsupported", unsupported), unsupported_raw)
            expected_unsupported_code = str(unsupported_raw["data"]["error"]["code"])
            _assert_failure(unsupported_observation, expected_code=expected_unsupported_code, expected_path=unsupported)
            assert unsupported_observation.error == unsupported_raw["error"]

            for suffix, expected_conversion, forbidden_conversion in (
                (".doc", ".docx", ".wordx"),
                (".xls", ".xlsx", ".excelx"),
            ):
                legacy_path = root / f"legacy{suffix}"
                legacy_path.write_bytes(b"legacy")
                legacy_raw = tool_read_document(str(legacy_path))
                legacy_observation = normalize_tool_result(
                    _envelope(f"legacy-{suffix}", legacy_path),
                    legacy_raw,
                )
                _assert_failure(
                    legacy_observation,
                    expected_code="document_legacy_format_unsupported",
                    expected_path=legacy_path,
                )
                assert expected_conversion in legacy_observation.error
                assert forbidden_conversion not in legacy_observation.error

            grounding = raw["metadata"]["path_grounding"]
            flat = normalize_tool_result(
                _envelope("legacy-flat", corrupt),
                {
                    "success": False,
                    "data": {
                        "path_grounding": grounding,
                        "error": {"code": "document_read_failed", "message": "legacy failure"},
                        "path": str(corrupt),
                    },
                },
            )
            _assert_failure(flat, expected_code="document_read_failed", expected_path=corrupt)
            assert flat.error == "legacy failure" and flat.metadata["path_grounding"] == grounding

            nested = normalize_tool_result(
                _envelope("legacy-nested", corrupt),
                {
                    "success": False,
                    "data": {
                        "result": {
                            "path_grounding": grounding,
                            "error": {"code": "document_read_failed", "message": "legacy nested failure"},
                            "path": str(corrupt),
                        }
                    },
                },
            )
            _assert_failure(nested, expected_code="document_read_failed", expected_path=corrupt)
            assert nested.error == "legacy nested failure" and nested.metadata["path_grounding"] == grounding

            grounding_a = {"marker": "A", "resolved_path": str(corrupt.resolve())}
            priority = normalize_tool_result(
                _envelope("priority", corrupt),
                {
                    "success": False,
                    "metadata": {"path_grounding": grounding_a},
                    "data": {
                        "path_grounding": {"marker": "B"},
                        "result": {
                            "path_grounding": {"marker": "C"},
                            "error": {"code": "document_read_failed", "message": "priority failure"},
                            "path": str(corrupt),
                        },
                    },
                },
            )
            _assert_failure(priority, expected_code="document_read_failed", expected_path=corrupt)
            assert priority.metadata["path_grounding"] == grounding_a
            assert priority.error == "priority failure"

            helper_payload = {"metadata": {"path_grounding": grounding_a}}
            helper_data = {"result": {"path": str(corrupt), "path_grounding": {"marker": "C"}}, "path_grounding": {"marker": "B"}}
            _partition_legacy_read_document_metadata(helper_payload, helper_data, argument_path=str(corrupt))
            once = json.dumps({"payload": helper_payload, "data": helper_data}, ensure_ascii=False, sort_keys=True)
            _partition_legacy_read_document_metadata(helper_payload, helper_data, argument_path=str(corrupt))
            twice = json.dumps({"payload": helper_payload, "data": helper_data}, ensure_ascii=False, sort_keys=True)
            assert once == twice
            assert not list(artifact_dir.glob("document_result_*.json"))
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_read_document_failure_observation ok")


if __name__ == "__main__":
    main()
