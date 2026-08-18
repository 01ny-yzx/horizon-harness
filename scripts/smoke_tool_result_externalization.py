"""Offline smoke checks for durable, redacted ToolResult references."""

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

from core.observation_compaction import compact_observation_for_model
from core.sandbox import SandboxManager
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result
from core.tool_result_store import ToolResultStore


def _envelope(call_id: str, tool: str, arguments: dict[str, object] | None = None) -> ToolCallEnvelope:
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name=tool,
        tool_name=tool,
        canonical_name=tool,
        executable_name=tool,
        raw_arguments=json.dumps(arguments or {}),
        parsed_arguments=dict(arguments or {}),
        sanitized_arguments=dict(arguments or {}),
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "task/../../../unsafe"},
    )


def test_store_integrity_and_safe_names() -> None:
    with TemporaryDirectory() as directory:
        store = ToolResultStore(directory)
        text = "line one\nline two"
        stored = store.store_text(text, task_id="../../task", call_id="../call", kind="result")
        path = Path(stored.content_ref)
        assert path.exists() and path.parent == Path(directory).resolve()
        assert path.read_text() == text
        assert stored.sha256 == hashlib.sha256(text.encode()).hexdigest()
        assert ".." not in path.name and "/" not in path.name


def test_generic_and_file_references() -> None:
    large = {"blob": "G" * 14000}
    generic = normalize_tool_result(_envelope("call-generic", "generic_tool"), {"success": True, "status": "success", "data": large})
    assert generic.content_externalized is True
    assert Path(generic.content_ref).exists()
    assert generic.content_sha256

    small = normalize_tool_result(_envelope("call-small", "generic_tool"), {"success": True, "status": "success", "data": {"value": "ok"}})
    assert small.content_externalized is False and not small.content_ref

    with TemporaryDirectory() as directory:
        source = Path(directory) / "source.txt"
        source.write_text("SOURCE BODY", encoding="utf-8")
        read = normalize_tool_result(
            _envelope("call-read", "read_file", {"path": str(source)}),
            {"success": True, "status": "success", "data": {"path": str(source), "content": "SOURCE BODY"}},
        )
        assert read.source_ref == str(source.resolve())
        assert not read.content_ref
        assert read.content_externalized is False


def test_sandbox_stdio_is_recoverable_and_redacted() -> None:
    secret = "SECRET_TOKEN_123456"
    previous = os.environ.get("SMOKE_SECRET_TOKEN")
    os.environ["SMOKE_SECRET_TOKEN"] = secret
    try:
        stdout = "prefix " + secret + "\n" + "O" * 16000
        stderr = "E" * 13000
        manager = SandboxManager()
        manager.max_output_chars = 1000
        stdio = manager._stdio_payload(stdout, stderr)
        observation = normalize_tool_result(
            _envelope("call-sandbox", "sandbox_exec", {"command": "python noisy.py"}),
            {
                "success": False,
                "status": "failed",
                "error_code": "command_failed",
                "data": {
                    "command": "python noisy.py",
                    "exit_code": 1,
                    **stdio,
                },
            },
        )
    finally:
        if previous is None:
            os.environ.pop("SMOKE_SECRET_TOKEN", None)
        else:
            os.environ["SMOKE_SECRET_TOKEN"] = previous
    assert observation.content_externalized is True
    assert len(observation.stdout) < len(stdout)
    assert observation.data["stdout_ref"] and observation.data["stderr_ref"]
    full_stdout = Path(observation.data["stdout_ref"]).read_text(encoding="utf-8")
    assert len(full_stdout) > 15000 and secret not in full_stdout and "[REDACTED]" in full_stdout
    compacted = compact_observation_for_model(observation)
    summary = compacted["model_visible_summary"]
    assert len(summary["stdout_preview"]) < 700
    assert Path(summary["stdout_ref"]).exists()
    assert summary["exit_code"] == 1 and summary["status"] == "failed"
    assert summary.get("content_sha256") or summary.get("stdout_sha256")


def main() -> None:
    test_store_integrity_and_safe_names()
    test_generic_and_file_references()
    test_sandbox_stdio_is_recoverable_and_redacted()
    print("smoke_tool_result_externalization ok")


if __name__ == "__main__":
    main()
