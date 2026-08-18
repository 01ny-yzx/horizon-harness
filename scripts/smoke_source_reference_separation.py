"""Smoke checks for strict Source Reference and Tool Artifact separation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true", "TOOL_RESULT_EXTERNALIZE_CHARS": "1000"}
_PREVIOUS = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

from core.file_access_policy import FileAccessDecision
from core.observation_compaction import build_tool_result_card
from core.source_reference import resolve_source_file_metadata
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_from_cache_snapshot, observation_to_cache_snapshot
from core.tool_result_store import ToolResultStore, resolve_tool_artifact_metadata
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _envelope(call_id: str, tool: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name=tool,
        tool_name=tool,
        canonical_name=tool,
        executable_name=tool,
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "source-separation"},
    )


def test_sources_and_artifacts(root: Path, workspace: Path) -> None:
    outside = root / "authorized-external"
    outside.mkdir()
    small_path = outside / "small.txt"
    small_path.write_text("small source", encoding="utf-8")
    before = set((workspace / "tool_results").glob("*")) if (workspace / "tool_results").exists() else set()
    small = normalize_tool_result(
        _envelope("small-read", "read_file", small_path),
        {"success": True, "status": "success", "metadata": {"path": str(small_path)}, "data": {"path": str(small_path), "content": "small source"}},
    )
    assert small.success and small.source_ref == str(small_path.resolve())
    assert not small.content_ref and small.source_kind == "file"
    assert set((workspace / "tool_results").glob("*")) == before
    assert build_tool_result_card(small)["source_ref"] == str(small_path.resolve())

    large_path = outside / "large.txt"
    large_body = "L" * 5000
    large_path.write_text(large_body, encoding="utf-8")
    large = normalize_tool_result(
        _envelope("large-read", "read_file", large_path),
        {"success": True, "status": "success", "metadata": {"path": str(large_path)}, "data": {"path": str(large_path), "content": large_body}},
    )
    assert large.source_ref == str(large_path.resolve()) and not large.content_ref
    assert len(large.output_text) < len(large_body)
    assert set((workspace / "tool_results").glob("*")) == before

    written = outside / "written.txt"
    written.write_text("written result", encoding="utf-8")
    write_observation = normalize_tool_result(
        _envelope("write", "write_file", written),
        {"success": True, "status": "success", "data": {"path": str(written), "bytes": written.stat().st_size}},
    )
    assert write_observation.source_ref == str(written.resolve()) and not write_observation.content_ref

    invalid_artifact = resolve_tool_artifact_metadata(small_path)
    assert invalid_artifact.error_code == "replay_artifact_invalid_ref"
    stored = ToolResultStore().store_text("internal artifact", task_id="task", call_id="call", kind="content")
    valid_artifact = resolve_tool_artifact_metadata(stored.content_ref)
    assert valid_artifact.valid and valid_artifact.sha256 == stored.sha256

    for status in ("failed", "blocked"):
        failed = normalize_tool_result(
            _envelope(f"{status}-read", "read_file", small_path),
            {"success": False, "status": status, "error": status, "data": {"path": str(small_path), "content": "LEAK"}},
        )
        assert not failed.source_ref and not failed.content_ref and "LEAK" not in json.dumps(failed.data)

    snapshot = observation_to_cache_snapshot(small)
    small_path.write_text("changed source", encoding="utf-8")
    changed = observation_from_cache_snapshot(_envelope("changed", "read_file", small_path), snapshot)
    assert not changed.success and changed.error_code == "replay_source_hash_mismatch"

    deleted_path = outside / "deleted.txt"
    deleted_path.write_text("delete me", encoding="utf-8")
    deleted_obs = normalize_tool_result(
        _envelope("deleted-original", "read_file", deleted_path),
        {"success": True, "status": "success", "data": {"path": str(deleted_path), "content": "delete me"}},
    )
    deleted_snapshot = observation_to_cache_snapshot(deleted_obs)
    deleted_path.unlink()
    deleted = observation_from_cache_snapshot(_envelope("deleted-replay", "read_file", deleted_path), deleted_snapshot)
    assert not deleted.success and deleted.error_code == "replay_source_missing"

    with patch("core.source_reference.FileAccessPolicy.evaluate", return_value=FileAccessDecision(False, "read", "denied", str(small_path), code="sensitive_file_blocked", reason="denied")):
        denied = resolve_source_file_metadata(small_path, operation="replay_read")
    assert denied.error_code == "replay_source_access_denied"

    document = outside / "document.md"
    document.write_text("original document", encoding="utf-8")
    derived = "parsed " * 1000
    document_obs = normalize_tool_result(
        _envelope("document", "read_document", document),
        {"success": True, "status": "success", "data": {"path": str(document), "text": derived}},
    )
    assert document_obs.source_ref == str(document.resolve()) and document_obs.source_kind == "document"
    assert document_obs.content_ref and Path(document_obs.content_ref).parent == workspace / "tool_results"
    assert document_obs.source_sha256 != document_obs.content_sha256


def main() -> None:
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            context = WorkspaceManager(root / "workspace-root").get_context("smoke", "source-reference")
            set_current_workspace(context)
            test_sources_and_artifacts(root, context.workspace_dir)
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_source_reference_separation ok")


if __name__ == "__main__":
    main()
