"""Smoke checks for failed read redaction and Artifact metadata integrity."""

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

_ENV_OVERRIDES = {
    "AGENT_ACCESS_MODE": "full_access",
    "TOOL_RESULT_EXTERNALIZE_CHARS": "12000",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}
os.environ.update(_ENV_OVERRIDES)

from core.observation_compaction import build_tool_result_card
from core.final_observation_context import build_final_observation_context
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_from_cache_snapshot, observation_to_cache_snapshot, observation_to_legacy_dict
from core.tool_result_store import ToolResultStore, resolve_text_artifact_metadata, validate_and_complete_text_artifact
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from tools.file_tools import read_file as tool_read_file


def _envelope(call_id: str, tool: str, arguments: dict[str, object]) -> ToolCallEnvelope:
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name=tool,
        tool_name=tool,
        canonical_name=tool,
        executable_name=tool,
        raw_arguments=json.dumps(arguments),
        parsed_arguments=dict(arguments),
        sanitized_arguments=dict(arguments),
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "task-failure"},
    )


def test_failed_and_blocked_reads(workspace: Path) -> None:
    existing = workspace / "blocked.txt"
    existing.write_text("SECRET-BODY" * 2000, encoding="utf-8")
    missing = workspace / "definitely-missing.txt"
    cases = tuple(
        (tool, status, path, code)
        for tool in ("read_file", "read_document")
        for status, path, code in (
            ("failed", missing, "file_not_found"),
            ("failed", existing, "read_failed"),
            ("blocked", existing, "sensitive_file_blocked"),
        )
    )
    forbidden = {"content", "text", "body", "markdown", "preview", "tables", "source_ref", "content_ref"}

    def assert_pure(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for item in value.values():
                assert_pure(item)
        elif isinstance(value, list):
            for item in value:
                assert_pure(item)

    for index, (tool, status, path, code) in enumerate(cases):
        observation = normalize_tool_result(
            _envelope(f"call-read-{index}", tool, {"path": str(path)}),
            {
                "success": False,
                "status": status,
                "error": "read denied",
                "error_code": code,
                "metadata": {
                    "path": str(path),
                    "path_grounding": {"operation": "read", "resolved_path": str(path), "allowed": status != "blocked", "content": "GROUNDING-LEAK"},
                    "content": "METADATA-LEAK",
                    "preview": "METADATA-PREVIEW",
                },
                "data": {"path": str(path), "content": "LEAK" * 5000, "text": "LEAK" * 5000, "body": "BODY-LEAK", "preview": "PREVIEW-LEAK", "content_ref": str(existing)},
            },
        )
        legacy = observation_to_legacy_dict(observation)
        card = build_tool_result_card(observation)
        state = SimpleNamespace(metadata={"completion_observations": [legacy]})
        final_context = build_final_observation_context(state, SimpleNamespace(tool=tool, policy_code="", metadata={}))
        rendered = json.dumps(card, ensure_ascii=False)
        assert observation.content_ref == "" and observation.output_text == ""
        assert "LEAK" not in rendered and "PREVIEW" not in rendered and "SECRET-BODY" not in rendered
        assert not card.get("content_ref")
        assert_pure(observation.data)
        assert_pure(legacy["data"])
        assert_pure(card)
        assert_pure(final_context)
        for key in ("content_ref", "content_sha256", "content_chars", "content_bytes", "content_externalized", "content", "text", "body", "preview"):
            assert key not in observation.data
        assert card["requested_path"] == str(path)
        assert card["error_code"] == code
        assert observation.error_code
        assert observation.metadata["path"] == str(path)
        assert observation.metadata["path_grounding"]["resolved_path"] == str(path)
        assert "content" not in observation.metadata["path_grounding"]
        assert observation.data["requested_path"] == str(path)
        assert observation.data["status"] == status
        assert observation.data["error"] == "read denied"
        assert observation.data["error_code"] == code

    real_missing = workspace / "real-missing.txt"
    real_raw = tool_read_file(str(real_missing))
    assert real_raw["success"] is False
    assert "path_grounding" not in real_raw["data"]
    assert isinstance(real_raw["metadata"].get("path_grounding"), dict)
    real_observation = normalize_tool_result(
        _envelope("call-real-missing", "read_file", {"path": str(real_missing)}),
        real_raw,
    )
    real_legacy = observation_to_legacy_dict(real_observation)
    real_card = build_tool_result_card(real_observation)
    real_state = SimpleNamespace(metadata={"completion_observations": [real_legacy]})
    real_final = build_final_observation_context(
        real_state,
        SimpleNamespace(tool="read_file", policy_code="", metadata={}),
    )
    assert real_observation.metadata["path"] == str(real_missing)
    assert isinstance(real_observation.metadata.get("path_grounding"), dict)
    for value in (real_observation.data, real_legacy["data"], real_card, real_final):
        assert_pure(value)
    assert real_observation.error and real_observation.error_code
    assert not real_observation.source_ref and not real_observation.content_ref

    success = normalize_tool_result(
        _envelope("call-read-success", "read_file", {"path": str(existing)}),
        {"success": True, "status": "success", "data": {"path": str(existing), "content": "visible"}},
    )
    success_card = build_tool_result_card(success)
    assert success_card["preview"] == "visible" and success_card["source_ref"] == str(existing.resolve())
    assert not success_card.get("content_ref")


def test_artifact_validation_and_replay(workspace: Path) -> None:
    text = "完整内容\nsecond line"
    store = ToolResultStore()
    artifact = Path(store.store_text(text, task_id="task", call_id="artifact", kind="content").content_ref)
    raw = artifact.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    valid = validate_and_complete_text_artifact(artifact, digest, len(text), len(raw))
    assert valid.valid and valid.chars == len(text) and valid.bytes == len(raw)
    completed = validate_and_complete_text_artifact(artifact, digest)
    assert completed.valid and completed.chars == len(text) and completed.bytes == len(raw)
    no_sha = resolve_text_artifact_metadata(artifact)
    assert no_sha.valid and no_sha.sha256 == digest and no_sha.chars == len(text) and no_sha.bytes == len(raw)
    assert validate_and_complete_text_artifact(artifact.parent / "missing", digest).error_code == "replay_artifact_missing"
    assert validate_and_complete_text_artifact(artifact, "0" * 64).error_code == "replay_artifact_hash_mismatch"
    assert validate_and_complete_text_artifact(artifact, digest, len(text) + 1).error_code == "replay_artifact_chars_mismatch"
    assert validate_and_complete_text_artifact(artifact, digest, None, len(raw) + 1).error_code == "replay_artifact_bytes_mismatch"
    assert validate_and_complete_text_artifact(Path("relative-artifact.txt")).error_code == "replay_artifact_invalid_ref"
    assert validate_and_complete_text_artifact(Path("/etc/hosts")).error_code == "replay_artifact_invalid_ref"

    stdout = Path(store.store_text("stdout body", task_id="task", call_id="stdout", kind="stdout").content_ref)
    stderr = Path(store.store_text("stderr body", task_id="task", call_id="stderr", kind="stderr").content_ref)
    observation = normalize_tool_result(
        _envelope("call-command", "sandbox_exec", {"command": "noop"}),
        {"success": True, "data": {
            "stdout": "preview",
            "stderr": "preview",
            "stdout_ref": str(stdout),
            "stderr_ref": str(stderr),
        }},
    )
    assert observation.data["stdout_chars"] == len("stdout body")
    assert observation.data["stderr_bytes"] == len(stderr.read_bytes())
    assert observation.data["stdout_sha256"] == hashlib.sha256(stdout.read_bytes()).hexdigest()
    assert observation.data["stderr_sha256"] == hashlib.sha256(stderr.read_bytes()).hexdigest()
    before = set((workspace / "tool_results").glob("*"))
    replay = observation_from_cache_snapshot(_envelope("call-command-replay", "sandbox_exec", {"command": "noop"}), observation_to_cache_snapshot(observation))
    after = set((workspace / "tool_results").glob("*"))
    assert replay.success and replay.data["stdout_ref"] == str(stdout.resolve())
    assert replay.data["stderr_ref"] == str(stderr.resolve()) and before == after

    content_observation = normalize_tool_result(
        _envelope("call-content-ref", "generic_tool", {}),
        {"success": True, "data": {"content_ref": str(artifact), "preview": "different preview"}},
    )
    assert content_observation.content_sha256 == digest
    assert content_observation.content_chars == len(text) and content_observation.content_bytes == len(raw)

    invalid_card = build_tool_result_card({
        "success": True,
        "status": "success",
        "tool": "generic_tool",
        "data": {"content_ref": str(artifact.parent / "missing-card.txt"), "preview": "untrusted"},
    })
    assert invalid_card["success"] is False
    assert invalid_card["error_code"] == "replay_artifact_missing"
    assert not invalid_card.get("content_ref")

    empty = Path(store.store_text("", task_id="task", call_id="empty", kind="empty").content_ref)
    empty_result = validate_and_complete_text_artifact(empty, hashlib.sha256(b"").hexdigest())
    assert empty_result.valid and empty_result.chars == 0 and empty_result.bytes == 0


def main() -> None:
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            context = WorkspaceManager(root).get_context("smoke", "failure-hardening")
            set_current_workspace(context)
            test_failed_and_blocked_reads(context.workspace_dir)
            test_artifact_validation_and_replay(context.workspace_dir)
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_failure_result_hardening ok")


if __name__ == "__main__":
    main()
