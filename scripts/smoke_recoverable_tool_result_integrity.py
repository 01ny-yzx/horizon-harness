"""Offline smoke checks for recoverable tool results and stable replay refs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

_ENV_OVERRIDES = {
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings
from core.context_budget import _tool_recovery_record, apply_context_budget
from core.observation_compaction import build_tool_result_card, compact_observation_for_model
from core.sandbox import SandboxManager
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import (
    is_recoverable_observation,
    normalize_tool_result,
    observation_from_cache_snapshot,
    observation_result,
    observation_to_cache_snapshot,
)
from core.tool_result_store import ToolResultStore
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


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
        metadata={"tool_spec_found": True, "task_id": "task-integrity"},
    )


def _chain(call_id: str, content_ref: str, sha256: str) -> list[dict[str, object]]:
    payload = {"call_id": call_id, "tool": "sandbox_exec", "success": True, "status": "success", "data": {"exit_code": 0, "content_ref": content_ref, "content_sha256": sha256}}
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "sandbox_exec", "arguments": "{}"}}]},
        {"role": "tool", "name": "sandbox_exec", "tool_call_id": call_id, "content": json.dumps(payload)},
    ]


def test_small_and_large_boundaries(workspace: Path) -> None:
    result_dir = workspace / "tool_results"
    before = set(result_dir.glob("*")) if result_dir.exists() else set()
    source = workspace / "small.txt"
    source.write_text("small body")
    small_read = normalize_tool_result(_envelope("call-read-small", "read_file"), {"success": True, "metadata": {"path": str(source)}, "data": {"content": "small body"}})
    assert small_read.data["content"] == "small body"
    assert not small_read.content_externalized
    assert set(result_dir.glob("*")) == before

    manager = SandboxManager()
    manager.max_output_chars = 1000
    small_stdio = manager._stdio_payload("small stdout", "")
    small_exec = normalize_tool_result(_envelope("call-small-exec", "sandbox_exec"), {"success": True, "data": small_stdio})
    assert small_exec.stdout == "small stdout" and not small_exec.content_ref and not small_exec.data["stdout_externalized"]

    stdout = "X" * (settings.tool_result_externalize_chars + 1000)
    large_exec = normalize_tool_result(_envelope("call-large-exec", "sandbox_exec"), {"success": True, "data": manager._stdio_payload(stdout, "")})
    assert Path(large_exec.data["stdout_ref"]).read_text() == stdout
    assert len(large_exec.stdout) < len(stdout)


def test_json_top_level_values_and_cards() -> None:
    values = (
        {"provider": "tavily", "enabled": True, "remaining_quota": 42, "message": "available"},
        ["a.py", "b.py"],
        "plain",
        7,
        True,
        None,
    )
    for index, value in enumerate(values):
        observation = normalize_tool_result(_envelope(f"call-value-{index}", "generic_tool"), value)
        assert observation_result(observation) == value

    listing = normalize_tool_result(_envelope("call-list", "list_files"), ["a.py", "b.py", "c.py"])
    card = build_tool_result_card(listing)
    assert card["item_count"] == 3 and card["items"] == ["a.py", "b.py", "c.py"]

    status = normalize_tool_result(_envelope("call-status-card", "get_usage_status"), values[0])
    status_card = build_tool_result_card(status)
    assert status_card["result"]["remaining_quota"] == 42
    history_card = _tool_recovery_record({
        "role": "tool",
        "name": "get_usage_status",
        "tool_call_id": "call-status-card",
        "content": json.dumps(status_card, ensure_ascii=False),
    })
    assert history_card == status_card and history_card["result"]["remaining_quota"] == 42

    large_value = {f"field_{index}": "V" * 200 for index in range(100)}
    large = normalize_tool_result(_envelope("call-large-json", "generic_tool"), large_value)
    large_card = build_tool_result_card(large, max_preview_chars=600)
    assert large.content_ref and Path(large.content_ref).exists()
    assert large_card.get("omitted_paths") and large_card["content_ref"] == large.content_ref


def test_store_reuse_and_replay() -> None:
    store = ToolResultStore()
    first = store.store_text("stable content", task_id="task", call_id="call", kind="stdout")
    second = store.store_text("stable content", task_id="task", call_id="call", kind="stdout")
    assert first.content_ref == second.content_ref and second.reused_existing is True

    stderr = store.store_text("stable error", task_id="task", call_id="call", kind="stderr")
    observation = normalize_tool_result(
        _envelope("call-original", "sandbox_exec"),
        {"success": True, "data": {
            "stdout": "preview",
            "stderr": "error preview",
            "stdout_ref": first.content_ref,
            "stdout_chars": first.chars,
            "stdout_bytes": first.bytes,
            "stdout_sha256": first.sha256,
            "stderr_ref": stderr.content_ref,
            "stderr_chars": stderr.chars,
            "stderr_bytes": stderr.bytes,
            "stderr_sha256": stderr.sha256,
        }},
    )
    snapshot = observation_to_cache_snapshot(observation)
    before = set(Path(first.content_ref).parent.glob("*"))
    replay = observation_from_cache_snapshot(_envelope("call-replay", "sandbox_exec"), snapshot)
    after = set(Path(first.content_ref).parent.glob("*"))
    assert replay.call_id == "call-replay" and replay.data["replayed_from_call_id"] == "call-original"
    for stream in ("stdout", "stderr"):
        for suffix in ("ref", "chars", "bytes", "sha256"):
            key = f"{stream}_{suffix}"
            assert replay.data[key] == observation.data[key]
    assert before == after

    legacy_snapshot = observation_to_cache_snapshot(observation)
    legacy_snapshot["data"].pop("stdout_sha256", None)
    legacy_snapshot["data"].pop("stdout_chars", None)
    legacy_snapshot["data"].pop("stdout_bytes", None)
    legacy_before = set(Path(first.content_ref).parent.glob("*"))
    legacy = observation_from_cache_snapshot(_envelope("call-legacy-replay", "sandbox_exec"), legacy_snapshot)
    legacy_after = set(Path(first.content_ref).parent.glob("*"))
    assert legacy.success and legacy.data["stdout_sha256"] == first.sha256
    assert legacy.data["stdout_chars"] == first.chars and legacy.data["stdout_bytes"] == first.bytes
    assert legacy_before == legacy_after

    invalid_snapshot = observation_to_cache_snapshot(observation)
    invalid_snapshot["data"]["stdout_ref"] = str(Path(first.content_ref).with_name("missing-stdout.txt"))
    invalid = observation_from_cache_snapshot(_envelope("call-invalid-replay", "sandbox_exec"), invalid_snapshot)
    assert not invalid.success and invalid.error_code == "replay_artifact_missing"


def test_recoverable_snapshot_compatibility() -> None:
    observation = normalize_tool_result(
        _envelope("call-recoverable", "read_file", {"path": "resource"}),
        {
            "success": False,
            "status": "failed",
            "error": "cannot decode",
            "error_code": "tool_resource_incompatible",
            "recoverable": True,
            "recovery_reason": "selected_text_reader_cannot_decode_resource",
            "data": {
                "requested_path": "resource",
                "error_code": "tool_resource_incompatible",
                "recoverable": True,
                "recovery_reason": "selected_text_reader_cannot_decode_resource",
            },
        },
    )
    replay = observation_from_cache_snapshot(
        _envelope("call-recoverable-replay", "read_file", {"path": "resource"}),
        observation_to_cache_snapshot(observation),
    )
    assert is_recoverable_observation(replay)
    assert replay.recovery_reason == observation.recovery_reason

    legacy_snapshot = observation_to_cache_snapshot(observation)
    legacy_snapshot.pop("recoverable", None)
    legacy_snapshot.pop("recovery_reason", None)
    legacy_snapshot["data"].pop("recoverable", None)
    legacy_snapshot["data"].pop("recovery_reason", None)
    legacy = observation_from_cache_snapshot(
        _envelope("call-old-snapshot", "read_file", {"path": "resource"}),
        legacy_snapshot,
    )
    assert legacy.recoverable is False and legacy.recovery_reason == ""


def test_generic_card_and_history_recovery(workspace: Path) -> None:
    generic = normalize_tool_result(
        _envelope("call-status", "get_usage_status"),
        {"success": True, "status": "success", "data": {"provider": "tavily", "enabled": True, "remaining_quota": 42, "message": "available"}},
    )
    summary = compact_observation_for_model(generic)["model_visible_summary"]
    assert summary["result"] == {"provider": "tavily", "enabled": True, "remaining_quota": 42, "message": "available"}

    ref = Path(ToolResultStore().store_text("recoverable", task_id="task", call_id="history", kind="content").content_ref)
    sha = hashlib.sha256(ref.read_bytes()).hexdigest()
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "old"}]
    for index in range(12):
        messages.extend(_chain(f"call-{index}", str(ref), sha))
    messages.extend([{"role": "assistant", "content": "old conclusion"}, {"role": "user", "content": "recent"}, {"role": "assistant", "content": "recent answer"}, {"role": "user", "content": "current"}])
    compacted, decision = apply_context_budget(messages, tools=[], runtime_lane="build", task_state=SimpleNamespace(task_id="task-history", metadata={}), model_context_tokens=2048, reserved_output_tokens=1024)
    rendered = json.dumps(compacted)
    manifest_ref = decision.metadata.get("history_tool_manifest_ref")
    assert manifest_ref and Path(manifest_ref).exists()
    manifest = Path(manifest_ref).read_text()
    assert "call-0" in manifest and str(ref) in manifest and sha in manifest
    assert "tool_name" in manifest and "compacted" in manifest
    assert "history_tool_manifest_ref" in rendered


def test_sandbox_character_threshold_and_independent_streams() -> None:
    manager = SandboxManager()
    manager.max_output_chars = 1000
    small = "中" * 5000
    small_observation = normalize_tool_result(_envelope("call-cn-small", "sandbox_exec"), {"success": True, "data": manager._stdio_payload(small, "")})
    assert not small_observation.data.get("stdout_ref")
    assert small_observation.data["stdout_chars"] == 5000
    assert small_observation.data["stdout_bytes"] == 15000

    stdout = "出" * 13000
    stderr = "错" * 14000
    large = normalize_tool_result(_envelope("call-cn-large", "sandbox_exec"), {"success": False, "data": manager._stdio_payload(stdout, stderr)})
    assert not large.content_ref and large.content_chars == 0 and large.content_sha256 == ""
    for stream, original in (("stdout", stdout), ("stderr", stderr)):
        ref = Path(large.data[f"{stream}_ref"])
        saved = ref.read_text(encoding="utf-8")
        assert saved == original
        assert large.data[f"{stream}_chars"] == len(original)
        assert large.data[f"{stream}_bytes"] == len(original.encode("utf-8"))
        assert large.data[f"{stream}_sha256"] == hashlib.sha256(original.encode("utf-8")).hexdigest()
        assert large.data[f"{stream}_externalized"] is True


def main() -> None:
    old_threshold = settings.tool_result_externalize_chars
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            os.chdir(directory)
            context = WorkspaceManager(Path(directory)).get_context("smoke", "integrity")
            set_current_workspace(context)
            object.__setattr__(settings, "tool_result_externalize_chars", 12000)
            test_small_and_large_boundaries(context.workspace_dir)
            test_json_top_level_values_and_cards()
            test_store_reuse_and_replay()
            test_recoverable_snapshot_compatibility()
            test_generic_card_and_history_recovery(context.workspace_dir)
            test_sandbox_character_threshold_and_independent_streams()
    finally:
        os.chdir(original_cwd)
        object.__setattr__(settings, "tool_result_externalize_chars", old_threshold)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_recoverable_tool_result_integrity ok")


if __name__ == "__main__":
    main()
