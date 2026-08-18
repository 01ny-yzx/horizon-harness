from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true", "TOOL_RESULT_EXTERNALIZE_CHARS": "500"}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.source_reference import resolve_source_file_metadata
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_from_cache_snapshot, observation_to_cache_snapshot
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, tool: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id, provider_call_id=call_id, source=ToolCallSource.STRUCTURED,
        raw_name=tool, tool_name=tool, canonical_name=tool, executable_name=tool,
        raw_arguments=json.dumps(arguments), parsed_arguments=arguments, sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE, metadata={"tool_spec_found": True, "task_id": "binary-source"},
    )


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            workspace = WorkspaceManager(root / "workspace").get_context("smoke", "binary-source")
            set_current_workspace(workspace)

            text_path = root / "note.txt"
            text_path.write_text("hello 世界", encoding="utf-8")
            text = resolve_source_file_metadata(text_path, operation="read")
            assert text.ok and text.is_text and text.encoding == "utf-8" and text.chars == 8
            assert text.bytes == len(text_path.read_bytes()) and text.sha256 == hashlib.sha256(text_path.read_bytes()).hexdigest()

            binaries = {
                "sample.pdf": b"%PDF-1.7\x00\xffbinary",
                "sample.docx": b"PK\x03\x04\x00\xffdocx",
                "sample.xlsx": b"PK\x03\x04\x00\xffxlsx",
            }
            for name, raw in binaries.items():
                path = root / name
                path.write_bytes(raw)
                resolved = resolve_source_file_metadata(path, operation="read")
                assert resolved.ok and not resolved.is_text and resolved.chars is None and resolved.encoding is None
                assert resolved.bytes == len(raw) and resolved.sha256 == hashlib.sha256(raw).hexdigest()
                assert resolved.error_code != "replay_source_decode_error"

            pdf = root / "sample.pdf"
            parsed = "PARSED-DOCUMENT-TEXT " * 100
            observation = normalize_tool_result(
                _envelope("document", "read_document", pdf),
                {"success": True, "status": "success", "data": {"path": str(pdf), "text": parsed}},
            )
            assert observation.source_ref == str(pdf.resolve()) and observation.source_chars is None
            assert observation.source_bytes == len(pdf.read_bytes()) and observation.source_is_text is False
            assert observation.content_ref and observation.content_sha256 != observation.source_sha256

            binary_snapshot = observation_to_cache_snapshot(observation)
            binary_snapshot["source_chars"] = 999
            binary_snapshot["data"]["source_chars"] = 999
            replayed_binary = observation_from_cache_snapshot(_envelope("binary-replay", "read_document", pdf), binary_snapshot)
            assert replayed_binary.success and replayed_binary.source_chars is None

            text_observation = normalize_tool_result(
                _envelope("text", "read_file", text_path),
                {"success": True, "status": "success", "data": {"path": str(text_path), "content": "hello 世界"}},
            )
            text_snapshot = observation_to_cache_snapshot(text_observation)
            text_snapshot["source_chars"] = 999
            text_snapshot["data"]["source_chars"] = 999
            replayed_text = observation_from_cache_snapshot(_envelope("text-replay", "read_file", text_path), text_snapshot)
            assert not replayed_text.success and replayed_text.error_code == "replay_source_chars_mismatch"
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_binary_source_metadata ok")


if __name__ == "__main__":
    main()
