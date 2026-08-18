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

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true"}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.source_reference import resolve_source_file_metadata
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_from_cache_snapshot, observation_to_cache_snapshot, observation_to_legacy_dict
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, tool: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id, provider_call_id=call_id, source=ToolCallSource.STRUCTURED,
        raw_name=tool, tool_name=tool, canonical_name=tool, executable_name=tool,
        raw_arguments=json.dumps(arguments), parsed_arguments=arguments, sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE, metadata={"tool_spec_found": True, "task_id": "non-utf8"},
    )


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "non-utf8"))

            utf8 = root / "utf8.txt"
            utf8.write_text("hello 世界", encoding="utf-8")
            utf8_meta = resolve_source_file_metadata(utf8, operation="read")
            assert utf8_meta.ok and utf8_meta.chars == 8 and utf8_meta.encoding == "utf-8" and utf8_meta.is_text

            latin = root / "latin.csv"
            latin_raw = "name,café\n".encode("latin-1")
            latin.write_bytes(latin_raw)
            latin_obs = normalize_tool_result(
                _envelope("latin", "read_file", latin),
                {"success": True, "status": "success", "data": "name,café\n"},
            )
            assert latin_obs.success and latin_obs.source_chars is None
            assert latin_obs.source_encoding == "unknown" and latin_obs.source_is_text is True
            assert latin_obs.source_bytes == len(latin_raw)
            assert latin_obs.source_sha256 == hashlib.sha256(latin_raw).hexdigest()
            assert latin_obs.data["result"] == "name,café\n"
            legacy = observation_to_legacy_dict(latin_obs)
            assert legacy["data"]["source_chars"] is None

            shift_jis = root / "shift-jis.txt"
            shift_jis.write_bytes("日本語".encode("shift_jis"))
            shift_meta = resolve_source_file_metadata(shift_jis, operation="read")
            assert shift_meta.ok and shift_meta.is_text and shift_meta.encoding == "unknown"
            assert shift_meta.error_code != "replay_source_decode_error"

            for name, raw in {
                "sample.pdf": b"%PDF-1.7\x00\xff",
                "sample.docx": b"PK\x03\x04\x00\xffdocx",
                "sample.xlsx": b"PK\x03\x04\x00\xffxlsx",
                "unknown.bin": b"\x00\xff\x81\xfe",
            }.items():
                path = root / name
                path.write_bytes(raw)
                metadata = resolve_source_file_metadata(path, operation="read")
                assert metadata.ok and not metadata.is_text and metadata.chars is None and metadata.encoding is None

            snapshot = observation_to_cache_snapshot(latin_obs)
            replay = observation_from_cache_snapshot(_envelope("latin-replay", "read_file", latin), snapshot)
            assert replay.success and replay.source_chars is None and replay.source_encoding == "unknown"
            latin.write_bytes("name,thé\n".encode("latin-1"))
            changed = observation_from_cache_snapshot(_envelope("latin-changed", "read_file", latin), snapshot)
            assert not changed.success and changed.error_code == "replay_source_hash_mismatch"
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_non_utf8_source_metadata ok")


if __name__ == "__main__":
    main()
