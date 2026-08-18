from __future__ import annotations

import hashlib
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

from core.final_observation_context import build_final_observation_context
from core.observation_compaction import build_tool_result_card
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_legacy_dict
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id, provider_call_id=call_id, source=ToolCallSource.STRUCTURED,
        raw_name="read_document", tool_name="read_document", canonical_name="read_document", executable_name="read_document",
        raw_arguments=json.dumps(arguments), parsed_arguments=arguments, sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE, metadata={"tool_spec_found": True, "task_id": "document-tables"},
    )


def _table(name: str, rows: int, columns: int, *, source_rows: int | None = None, source_columns: int | None = None) -> dict[str, object]:
    table: dict[str, object] = {
        "sheet_name": name,
        "headers": [f"H{column}" for column in range(columns)],
        "rows": [[f"{name}-R{row}-C{column}" for column in range(columns)] for row in range(rows)],
    }
    if source_rows is not None and source_columns is not None:
        table.update({
            "source_row_count": source_rows,
            "source_column_count": source_columns,
            "parsed_row_count": rows,
            "parsed_column_count": columns,
            "reader_omitted_rows": max(0, source_rows - rows),
            "reader_omitted_columns": max(0, source_columns - columns),
            "reader_truncated": source_rows > rows or source_columns > columns,
        })
    return table


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "document-tables"))
            source = root / "book.xlsx"
            source.write_bytes(b"PK\x03\x04xlsx-source")

            small_result = {"path": str(source), "tables": [_table("Small", 2, 2)], "text": "small workbook"}
            small = normalize_tool_result(_envelope("small", source), {"success": True, "status": "success", "data": small_result})
            assert small.success and not small.content_ref and small.compacted is False
            small_table = small.data["result"]["tables"][0]
            assert small_table["preview_rows"] == small_result["tables"][0]["rows"]
            assert not any(key in small_table for key in ("rows", "data", "values"))
            assert small_table["visible_row_count"] == 2 and small_table["visible_column_count"] == 2
            assert small_table["omitted_rows"] == 0 and small_table["omitted_columns"] == 0
            assert small_table["truncated"] is False

            tables = [_table(f"Sheet{index}", 50, 20, source_rows=100, source_columns=30) for index in range(1, 6)]
            full_result = {
                "path": str(source),
                "text": "parsed workbook",
                "tables": tables,
                "total_sheet_count": 7,
                "parsed_sheet_count": 5,
                "reader_omitted_sheets": 2,
                "reader_truncated": True,
                "metadata": {"producer": "smoke", "total_sheet_count": 7, "parsed_sheet_count": 5, "reader_omitted_sheets": 2},
            }
            observation = normalize_tool_result(_envelope("large", source), {"success": True, "status": "success", "data": full_result})
            canonical = observation.data["result"]
            assert observation.success and observation.compacted and observation.content_ref
            assert observation.source_ref == str(source.resolve()) and observation.source_is_text is False
            assert observation.source_sha256 != observation.content_sha256
            assert canonical["table_count"] == canonical["total_sheet_count"] == 7
            assert canonical["parsed_sheet_count"] == 5 and len(canonical["tables"]) <= 5
            assert canonical["reader_omitted_tables"] == 2
            assert canonical["omitted_tables"] == 7 - len(canonical["tables"])
            assert canonical["tables"] and canonical["visible_table_count"] >= 1
            first = canonical["tables"][0]
            assert first["row_count"] == 100 and first["column_count"] == 30
            assert first["parsed_row_count"] == 50 and first["parsed_column_count"] == 20
            assert len(first["preview_rows"]) <= 8 and all(len(row) <= 12 for row in first["preview_rows"])
            assert first["reader_omitted_rows"] == 50 and first["reader_omitted_columns"] == 10
            assert first["omitted_rows"] == 100 - len(first["preview_rows"])
            assert first["omitted_columns"] == 30 - first["visible_column_count"] and first["truncated"] is True
            assert first["projection_omitted_rows"] == 50 - first["visible_row_count"]
            assert first["projection_omitted_columns"] == 20 - first["visible_column_count"]
            assert '"rows":' not in json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))

            artifact_raw = Path(observation.content_ref).read_bytes()
            artifact = json.loads(artifact_raw.decode("utf-8"))
            assert artifact == {"result": full_result}
            assert len(artifact["result"]["tables"]) == 5
            assert artifact["result"]["reader_omitted_sheets"] == 2
            assert len(artifact["result"]["tables"][0]["rows"]) == 50
            assert len(artifact["result"]["tables"][0]["rows"][0]) == 20
            assert "Sheet6" not in json.dumps(artifact, ensure_ascii=False)
            assert hashlib.sha256(artifact_raw).hexdigest() == observation.content_sha256
            assert "source_ref" not in artifact and "call_id" not in artifact

            card = build_tool_result_card(observation)
            rendered_card = json.dumps(card, ensure_ascii=False)
            assert "Sheet1-R49-C19" not in rendered_card and len(rendered_card) < 6000
            assert card["content_ref"] == observation.content_ref and card["compacted"] is True
            state = SimpleNamespace(metadata={"completion_observations": [observation_to_legacy_dict(observation)]})
            final_context = build_final_observation_context(state, SimpleNamespace(tool="read_document", policy_code="", metadata={}))
            assert "Sheet1-R49-C19" not in json.dumps(final_context, ensure_ascii=False)

            repeated = normalize_tool_result(_envelope("large-repeat", source), {"success": True, "status": "success", "data": full_result})
            assert repeated.content_ref == observation.content_ref
            assert repeated.content_sha256 == observation.content_sha256 and repeated.data["content_ref_reused"] is True
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_document_table_compaction ok")


if __name__ == "__main__":
    main()
