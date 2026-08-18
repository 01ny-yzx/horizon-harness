from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import zipfile

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

from core.document_readers import read_document as reader_read_document
from core.final_observation_context import build_final_observation_context
from core.observation_compaction import build_tool_result_card
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_legacy_dict
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.file_tools import read_document as tool_read_document


def _column_name(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _write_xlsx(path: Path, *, sheets: int, rows: int, columns: int, small: bool = False) -> None:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook_sheets = "".join(f'<sheet name="Sheet{sheet}" sheetId="{sheet}"/>' for sheet in range(1, sheets + 1))
    small_values = (("A", "B"), ("C", "D"))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{namespace}"><sheets>{workbook_sheets}</sheets></workbook>')
        for sheet in range(1, sheets + 1):
            row_xml = []
            for row in range(1, rows + 1):
                cells = []
                for column in range(1, columns + 1):
                    value = small_values[row - 1][column - 1] if small else f"S{sheet}-R{row}-C{column}"
                    cells.append(f'<c r="{_column_name(column)}{row}" t="inlineStr"><is><t>{value}</t></is></c>')
                row_xml.append(f'<row r="{row}">{"".join(cells)}</row>')
            archive.writestr(
                f"xl/worksheets/sheet{sheet}.xml",
                f'<worksheet xmlns="{namespace}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>',
            )


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
        metadata={"tool_spec_found": True, "task_id": "metadata-partition"},
    )


def _final_context(observation: object) -> list[dict[str, object]]:
    legacy = observation_to_legacy_dict(observation)  # type: ignore[arg-type]
    state = SimpleNamespace(metadata={"completion_observations": [legacy]})
    return build_final_observation_context(state, SimpleNamespace(tool="read_document", policy_code="", metadata={}))


def _assert_small_observation(observation: object) -> None:
    assert observation.success is True  # type: ignore[attr-defined]
    assert observation.compacted is False  # type: ignore[attr-defined]
    assert observation.data["document_result_compacted"] is False  # type: ignore[attr-defined]
    assert not observation.content_ref  # type: ignore[attr-defined]
    assert "content_ref" not in observation.data  # type: ignore[attr-defined]
    assert "path_grounding" not in observation.data  # type: ignore[attr-defined]
    assert "path_grounding" not in observation.data["result"]  # type: ignore[attr-defined]
    assert isinstance(observation.metadata.get("path_grounding"), dict)  # type: ignore[attr-defined]
    assert observation.metadata.get("path")  # type: ignore[attr-defined]
    table = observation.data["result"]["tables"][0]  # type: ignore[attr-defined]
    assert table["preview_rows"] == [["A", "B"], ["C", "D"]]
    assert table["visible_row_count"] == 2 and table["visible_column_count"] == 2
    assert table["omitted_rows"] == 0 and table["omitted_columns"] == 0
    assert table["truncated"] is False


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            workspace = WorkspaceManager(root / "workspace").get_context("smoke", "metadata-partition")
            set_current_workspace(workspace)
            artifact_dir = workspace.workspace_dir / "tool_results"

            small_path = root / "small.xlsx"
            _write_xlsx(small_path, sheets=1, rows=2, columns=2, small=True)
            raw = tool_read_document(str(small_path))
            assert raw["success"] is True and "path_grounding" not in raw["data"]
            assert raw["metadata"]["path"] == str(small_path.resolve())
            assert isinstance(raw["metadata"]["path_grounding"], dict)
            assert isinstance(raw["data"]["metadata"], dict)
            assert "path_grounding" not in raw["data"]["metadata"]
            small = normalize_tool_result(_envelope("small", small_path), raw)
            _assert_small_observation(small)
            assert small.source_ref == str(small_path.resolve())
            assert not list(artifact_dir.glob("document_result_*.json"))
            card = build_tool_result_card(small)
            assert isinstance(card.get("path_grounding"), dict)
            assert card["path_grounding"]["resolved_path"] == str(small_path.resolve())
            final_context = _final_context(small)
            for rendered in (json.dumps(card, ensure_ascii=False), json.dumps(final_context, ensure_ascii=False)):
                assert all(cell in rendered for cell in ("A", "B", "C", "D"))

            reader = reader_read_document(small_path)
            legacy_data = reader.to_dict()
            legacy_data["path_grounding"] = raw["metadata"]["path_grounding"]
            legacy = normalize_tool_result(
                _envelope("legacy-flat", small_path),
                {"success": True, "status": "success", "data": legacy_data},
            )
            _assert_small_observation(legacy)
            assert not list(artifact_dir.glob("document_result_*.json"))

            nested_data = reader.to_dict()
            nested_data["path_grounding"] = raw["metadata"]["path_grounding"]
            nested = normalize_tool_result(
                _envelope("legacy-nested", small_path),
                {"success": True, "status": "success", "data": {"result": nested_data}},
            )
            _assert_small_observation(nested)

            grounding_a = {"marker": "A", "resolved_path": str(small_path)}
            grounding_b = {"marker": "B"}
            grounding_c = {"marker": "C"}
            priority_data = reader.to_dict()
            priority_data["path_grounding"] = grounding_c
            priority = normalize_tool_result(
                _envelope("priority", small_path),
                {
                    "success": True,
                    "status": "success",
                    "metadata": {"path_grounding": grounding_a},
                    "data": {"result": priority_data, "path_grounding": grounding_b},
                },
            )
            assert priority.metadata["path_grounding"] == grounding_a
            assert "path_grounding" not in priority.data and "path_grounding" not in priority.data["result"]
            assert "path_grounding" not in priority.metadata["path_grounding"]

            corrupt_path = root / "corrupt.xlsx"
            corrupt_path.write_bytes(b"not-a-valid-xlsx")
            corrupt_raw = tool_read_document(str(corrupt_path))
            assert corrupt_raw["success"] is False
            assert isinstance(corrupt_raw["metadata"]["path_grounding"], dict)
            corrupt = normalize_tool_result(_envelope("corrupt", corrupt_path), corrupt_raw)
            assert (
                corrupt.success is False
                and corrupt.error_code == "document_xlsx_invalid_or_corrupt"
            )
            assert isinstance(corrupt.metadata.get("path_grounding"), dict)
            assert "path_grounding" not in corrupt.data and "path_grounding" not in corrupt.data["result"]
            assert not corrupt.source_ref and not corrupt.content_ref

            large_path = root / "large.xlsx"
            _write_xlsx(large_path, sheets=7, rows=100, columns=30)
            large_raw = tool_read_document(str(large_path))
            assert large_raw["success"] is True and "path_grounding" not in large_raw["data"]
            assert isinstance(large_raw["metadata"]["path_grounding"], dict)
            large = normalize_tool_result(_envelope("large", large_path), large_raw)
            assert large.success and large.compacted and large.content_ref and large.source_ref
            assert isinstance(large.metadata.get("path_grounding"), dict)
            canonical = large.data["result"]
            assert canonical["tables"] and canonical["visible_table_count"] >= 1
            first = canonical["tables"][0]
            assert first["source_row_count"] == 100 and first["parsed_row_count"] == 50
            assert first["visible_row_count"] >= 1
            assert first["reader_omitted_rows"] == 50
            assert first["projection_omitted_rows"] == 50 - first["visible_row_count"]
            assert first["omitted_rows"] == 100 - first["visible_row_count"]
            assert "S1-R100-C30" not in json.dumps(canonical, ensure_ascii=False)
            artifact = json.loads(Path(large.content_ref).read_text(encoding="utf-8"))
            assert set(artifact) == {"result"}
            assert "path_grounding" not in json.dumps(artifact, ensure_ascii=False)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_read_document_metadata_partition ok")


if __name__ == "__main__":
    main()
