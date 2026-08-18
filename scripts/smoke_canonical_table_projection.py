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

from core.document_readers import read_document
from core.document_result_compaction import compact_document_result
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


def _write_xlsx(path: Path, *, sheets: int, rows: int, columns: int, value_fn: object) -> None:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook_sheets = "".join(f'<sheet name="Sheet{sheet}" sheetId="{sheet}"/>' for sheet in range(1, sheets + 1))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{namespace}"><sheets>{workbook_sheets}</sheets></workbook>')
        for sheet in range(1, sheets + 1):
            row_xml = []
            for row in range(1, rows + 1):
                cells = "".join(
                    f'<c r="{_column_name(column)}{row}" t="inlineStr"><is><t>{value_fn(sheet, row, column)}</t></is></c>'  # type: ignore[operator]
                    for column in range(1, columns + 1)
                )
                row_xml.append(f'<row r="{row}">{cells}</row>')
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
        metadata={"tool_spec_found": True, "task_id": "canonical-table"},
    )


def _final_context(observation: object) -> list[dict[str, object]]:
    legacy = observation_to_legacy_dict(observation)  # type: ignore[arg-type]
    state = SimpleNamespace(metadata={"completion_observations": [legacy]})
    outcome = SimpleNamespace(tool="read_document", policy_code="", metadata={})
    return build_final_observation_context(state, outcome)


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "canonical-table"))

            small_path = root / "small.xlsx"
            small_values = (("A", "B"), ("C", "D"))
            _write_xlsx(
                small_path,
                sheets=1,
                rows=2,
                columns=2,
                value_fn=lambda _sheet, row, column: small_values[row - 1][column - 1],
            )
            tool_raw = tool_read_document(str(small_path))
            assert "path_grounding" not in tool_raw["data"]
            assert isinstance(tool_raw.get("metadata", {}).get("path_grounding"), dict)
            assert isinstance(tool_raw["data"].get("metadata"), dict)
            small_reader = read_document(small_path, max_chars=12000, max_sheets=5, max_table_rows=50, max_table_cols=20)
            small = normalize_tool_result(
                _envelope("small", small_path),
                {"success": True, "status": "success", "data": small_reader.to_dict()},
            )
            small_table = small.data["result"]["tables"][0]
            assert small.success and small.compacted is False and not small.content_ref
            assert small_table["preview_rows"] == [["A", "B"], ["C", "D"]]
            assert not any(key in small_table for key in ("rows", "data", "values"))
            assert small_table["visible_row_count"] == 2 and small_table["visible_column_count"] == 2
            assert small_table["omitted_rows"] == 0 and small_table["omitted_columns"] == 0
            small_card = build_tool_result_card(small)
            small_final = _final_context(small)
            for rendered in (json.dumps(small_card, ensure_ascii=False), json.dumps(small_final, ensure_ascii=False)):
                assert all(cell in rendered for cell in ("A", "B", "C", "D"))

            large_path = root / "large.xlsx"
            _write_xlsx(
                large_path,
                sheets=7,
                rows=100,
                columns=30,
                value_fn=lambda sheet, row, column: f"S{sheet}-R{row}-C{column}",
            )
            large_reader = read_document(large_path, max_chars=12000, max_sheets=5, max_table_rows=50, max_table_cols=20)
            large = normalize_tool_result(
                _envelope("large", large_path),
                {"success": True, "status": "success", "data": large_reader.to_dict()},
            )
            canonical = large.data["result"]
            assert large.success and large.compacted and large.source_ref and large.content_ref
            assert canonical["table_count"] == 7 and canonical["parsed_sheet_count"] == 5
            assert canonical["tables"] and canonical["visible_table_count"] >= 1
            first = canonical["tables"][0]
            assert first["preview_rows"] and first["visible_row_count"] >= 1 and first["visible_column_count"] >= 1
            for key in (
                "source_row_count", "source_column_count", "parsed_row_count", "parsed_column_count",
                "reader_omitted_rows", "projection_omitted_rows", "omitted_rows",
                "reader_omitted_columns", "projection_omitted_columns", "omitted_columns",
            ):
                assert key in first
            rendered = json.dumps(canonical, ensure_ascii=False)
            assert "S1-R100-C30" not in rendered and "S1-R50-C20" not in rendered
            assert len(json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True)) <= 1200
            large_final = _final_context(large)
            final_summary = large_final[0]["data_summary"]
            assert final_summary["tables"] and final_summary["visible_table_count"] >= 1
            assert final_summary["tables"][0]["preview_rows"][0][0] == "S1-R1-C1"

            pressure = compact_document_result(
                {
                    "text": "long excerpt " * 1000,
                    "tables": [{"name": "Pressure", "rows": [[f"cell-{row}-{column}" for column in range(12)] for row in range(8)]}],
                },
                max_preview_chars=1200,
                max_table_rows=8,
                max_table_columns=12,
                max_tables=5,
            )
            pressure_result = pressure.compacted_result
            assert pressure.was_compacted and pressure_result["tables"]
            assert pressure_result["visible_table_count"] >= 1
            assert pressure_result["tables"][0]["preview_rows"]
            assert len(json.dumps(pressure_result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)) <= 1200
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_canonical_table_projection ok")


if __name__ == "__main__":
    main()
