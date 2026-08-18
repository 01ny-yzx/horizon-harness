from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.document_readers import read_document
from core.document_result_compaction import compact_document_result


def _column_name(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _write_xlsx(path: Path, *, sheets: int = 7, rows: int = 100, columns: int = 30) -> None:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook_sheets = "".join(
        f'<sheet name="Sheet{sheet}" sheetId="{sheet}"/>' for sheet in range(1, sheets + 1)
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{namespace}"><sheets>{workbook_sheets}</sheets></workbook>',
        )
        for sheet in range(1, sheets + 1):
            row_xml = []
            for row in range(1, rows + 1):
                cells = "".join(
                    f'<c r="{_column_name(column)}{row}" t="inlineStr"><is><t>S{sheet}-R{row}-C{column}</t></is></c>'
                    for column in range(1, columns + 1)
                )
                row_xml.append(f'<row r="{row}">{cells}</row>')
            archive.writestr(
                f"xl/worksheets/sheet{sheet}.xml",
                f'<worksheet xmlns="{namespace}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>',
            )


def main() -> None:
    with TemporaryDirectory() as directory:
        source = Path(directory) / "counts.xlsx"
        _write_xlsx(source)
        result = read_document(
            source,
            max_chars=1_000_000,
            max_sheets=5,
            max_table_rows=50,
            max_table_cols=20,
        )
        assert result.success
        assert result.sheet_count == result.total_sheet_count == 7
        assert result.parsed_sheet_count == 5
        assert result.reader_omitted_sheets == 2
        assert result.reader_truncated is True and result.truncated is True
        assert len(result.tables) == 5
        first = result.tables[0]
        assert "rows" in first and "preview_rows" not in first
        assert first["source_row_count"] == 100
        assert first["source_column_count"] == 30
        assert first["parsed_row_count"] == 50
        assert first["parsed_column_count"] == 20
        assert first["reader_omitted_rows"] == 50
        assert first["reader_omitted_columns"] == 10
        assert first["reader_truncated"] is True
        assert first["rows"][49][19] == "S1-R50-C20"

        compacted = compact_document_result(
            result.to_dict(),
            max_preview_chars=12_000,
            max_table_rows=8,
            max_table_columns=12,
            max_tables=5,
        )
        assert compacted.was_compacted and compacted.full_result_required
        summary = compacted.compacted_result
        assert summary["table_count"] == summary["total_sheet_count"] == 7
        assert summary["parsed_sheet_count"] == 5
        assert summary["visible_table_count"] <= 5
        assert summary["reader_omitted_tables"] == 2
        assert summary["omitted_tables"] >= 2
        table = summary["tables"][0]
        assert "preview_rows" in table and not any(key in table for key in ("rows", "data", "values"))
        assert table["row_count"] == table["source_row_count"] == 100
        assert table["column_count"] == table["source_column_count"] == 30
        assert table["parsed_row_count"] == 50 and table["parsed_column_count"] == 20
        assert table["visible_row_count"] == 8 and table["visible_column_count"] == 12
        assert table["reader_omitted_rows"] == 50
        assert table["projection_omitted_rows"] == 42
        assert table["omitted_rows"] == 92
        assert table["reader_omitted_columns"] == 10
        assert table["projection_omitted_columns"] == 8
        assert table["omitted_columns"] == 18
        assert "S1-R100-C30" not in str(summary)
    print("smoke_xlsx_reader_count_fidelity ok")


if __name__ == "__main__":
    main()
