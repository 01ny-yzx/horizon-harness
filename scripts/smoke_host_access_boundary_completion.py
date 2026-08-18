"""Smoke checks for the completed host document and execution boundaries."""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.command_execution_context import host_command_context
from core.document_readers import read_document
from core.sandbox import SandboxManager
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.chunk_tools import rebuild_chunks_for_document
from tools.document_tools import load_document, load_documents_from_directory


def _write_xlsx(path: Path, value: str = "A") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{namespace}"><sheets><sheet name="Sheet1" sheetId="1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{namespace}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{value}</t></is></c></row></sheetData></worksheet>',
        )


def _code(result: dict[str, object]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "")
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return str(data["error"].get("code") or "")
    return str(result.get("error_code") or "")


def main() -> None:
    saved = {key: os.environ.get(key) for key in ("AGENT_ACCESS_MODE", "HORIZON_USER_DATA_ROOT")}
    original_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            external = base / "external"
            sandbox = base / "runtime-temp"
            project.mkdir()
            external.mkdir()
            sandbox.mkdir()
            os.chdir(project)
            os.environ["AGENT_ACCESS_MODE"] = "full_access"
            os.environ["HORIZON_USER_DATA_ROOT"] = str(base / "userdata")
            workspace = WorkspaceManager(base / "workspace").get_context("smoke", "host-boundary")
            set_current_workspace(workspace)

            special_paths = [
                external / ".git" / "report.xlsx",
                external / "node_modules" / "report.xlsx",
                external / ".env" / "report.xlsx",
                external / "memory_store" / "report.xlsx",
            ]
            for index, path in enumerate(special_paths, start=1):
                _write_xlsx(path, f"special-{index}")
                result = read_document(path)
                assert result.success, result
                assert not result.error or result.error.get("code") != "document_sensitive_file_blocked"

            relative = os.path.relpath(special_paths[0], project)
            assert ".." in relative
            assert read_document(relative).success

            missing = read_document(external / "missing.xlsx")
            assert not missing.success and missing.error["code"] == "document_file_not_found"
            directory_result = read_document(external)
            assert not directory_result.success and directory_result.error["code"] == "document_path_is_directory"
            unsupported = external / "unsupported.rtf"
            unsupported.write_text("x", encoding="utf-8")
            unsupported_result = read_document(unsupported)
            assert not unsupported_result.success and unsupported_result.error["code"] == "document_unsupported_type"
            oversized = external / "oversized.txt"
            oversized.write_text("12345", encoding="utf-8")
            oversized_result = read_document(oversized, max_file_bytes=4)
            assert not oversized_result.success and oversized_result.error["code"] == "document_file_too_large"
            corrupt = external / "corrupt.xlsx"
            corrupt.write_bytes(b"not-a-workbook")
            corrupt_result = read_document(corrupt)
            assert (
                not corrupt_result.success
                and corrupt_result.error["code"]
                == "document_xlsx_invalid_or_corrupt"
            )

            loaded = load_document(str(special_paths[0]), create_chunks=True)
            assert loaded["success"] is True, loaded
            assert loaded["metadata"]["path"] == str(special_paths[0].resolve())
            assert loaded["metadata"]["path_grounding"]["resolved_path"] == str(special_paths[0].resolve())
            document_id = str(loaded["data"]["document_id"])
            assert loaded["data"]["chunks_created"] is True

            batch_root = external / "batch"
            for name in (".git", ".venv", "node_modules", ".env"):
                _write_xlsx(batch_root / name / f"{name.strip('.') or 'env'}.xlsx", name)
            (batch_root / "unsupported.bin").write_bytes(b"binary")
            batch = load_documents_from_directory(str(batch_root), recursive=True, max_files=3, create_chunks=False)
            assert batch["success"] is True, batch
            assert batch["data"]["loaded_count"] == 3
            assert batch["data"]["skipped_count"] >= 1
            assert any("unsupported.bin" in str(item.get("path")) for item in batch["data"]["skipped"])
            assert batch["metadata"]["path_grounding"]["resolved_path"] == str(batch_root.resolve())

            rebuilt = rebuild_chunks_for_document(document_id)
            assert rebuilt["success"] is True, rebuilt
            assert rebuilt["metadata"]["path_grounding"]["resolved_path"] == str(special_paths[0].resolve())

            os.environ["AGENT_ACCESS_MODE"] = "read_only"
            assert read_document(special_paths[1]).success
            sentinel = external / "sentinel.txt"
            python_file = external / "side_effect.py"
            python_file.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n", encoding="utf-8")
            manager = SandboxManager()
            before_scripts = set(sandbox.glob("snippet_*.py"))
            with host_command_context(
                project_root=project,
                session_directory=project,
                sandbox_dir=sandbox,
                access_mode="read_only",
                runtime_lane="command_exec",
                task_id="read-only",
                source="smoke",
            ):
                blocked_command = manager.run_local_command(f"touch {sentinel}")
                blocked_code = manager.run_python_code(f"from pathlib import Path; Path({str(sentinel)!r}).write_text('bad')")
                blocked_file = manager.run_python_file(str(python_file))
            for result in (blocked_command, blocked_code, blocked_file):
                assert result["success"] is False
                assert result["error_code"] == "agent_access_mode_read_only"
            assert not sentinel.exists()
            assert set(sandbox.glob("snippet_*.py")) == before_scripts

            os.environ["AGENT_ACCESS_MODE"] = "full_access"
            with host_command_context(
                project_root=project,
                session_directory=project,
                sandbox_dir=sandbox,
                access_mode="full_access",
                runtime_lane="command_exec",
                task_id="full-access",
                source="smoke",
            ):
                allowed = manager.run_local_command("printf host-ok")
            assert allowed["success"] is True and "host-ok" in allowed["data"]["stdout"]
    finally:
        os.chdir(original_cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_host_access_boundary_completion: PASS")


if __name__ == "__main__":
    main()
