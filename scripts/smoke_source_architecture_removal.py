"""Offline closure smoke for Source Architecture removal."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_boundary import _sanitize_write_file_content
from core.source_reference import resolve_source_file_metadata
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result
from core.web_search_provider import _normalize_search_result
from prompts.system_prompt import build_system_prompt


REMOVED_MODULES = (
    ROOT / "core" / "sources.py",
    ROOT / "core" / "source_consistency.py",
    ROOT / "core" / "evidence_quality.py",
)
REMOVED_IMPORTS = (
    "core.sources",
    "core.source_consistency",
    "core.evidence_quality",
)
REMOVED_AUTHORITY_MARKERS = (
    "SourceStore",
    "SourceRecord",
    "SourceLifecycleStatus",
    "used_in_answer",
    "lifecycle_status",
    "mark_answer_used_sources",
    "bucket_final_answer_sources",
    "review_research_final_draft",
)
PROMPT_EVAL_LEGACY_MARKERS = (
    "SourceStore",
    "SourceRecord",
    "needs_official_docs",
    "sources_used",
    "source_type",
    "source_required",
    "browser sources",
)


def _envelope(tool: str, arguments: dict[str, object]) -> ToolCallEnvelope:
    raw = json.dumps(arguments, ensure_ascii=False)
    return ToolCallEnvelope(
        call_id=f"call-{tool}",
        provider_call_id=f"call-{tool}",
        source=ToolCallSource.STRUCTURED,
        raw_name=tool,
        tool_name=tool,
        canonical_name=tool,
        executable_name=tool,
        raw_arguments=raw,
        parsed_arguments=dict(arguments),
        sanitized_arguments=dict(arguments),
        status=ToolCallStatus.EXECUTABLE,
    )


def test_removed_modules_and_imports() -> None:
    assert all(not path.exists() for path in REMOVED_MODULES)
    for root_name in ("core", "tools", "workflows", "prompts", "evals"):
        for path in (ROOT / root_name).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not any(marker in source for marker in REMOVED_IMPORTS), path
            if root_name in {"core", "tools", "workflows"}:
                assert not any(marker in source for marker in REMOVED_AUTHORITY_MARKERS), path


def test_prompts_and_evals_have_no_source_authority() -> None:
    for root_name in ("prompts", "evals"):
        for path in (ROOT / root_name).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not any(marker in source for marker in PROMPT_EVAL_LEGACY_MARKERS), path
    prompt = build_system_prompt()
    assert "SourceStore" not in prompt
    assert "SourceRecord" not in prompt


def test_web_search_has_no_source_classification() -> None:
    for url in (
        "https://docs.example.com/api",
        "https://github.com/example/repo",
        "https://reddit.com/example",
    ):
        result = _normalize_search_result({"title": "Result", "url": url, "content": "Body", "score": 0.8}).to_dict()
        assert result["url"] == url
        assert not {"source_type", "is_official", "official_score"}.intersection(result)


def test_web_observations_remain_execution_facts() -> None:
    success = normalize_tool_result(
        _envelope("fetch_url", {"url": "http://example.com"}),
        {
            "success": True,
            "data": {
                "url": "http://example.com",
                "content_type": "text/plain",
                "format": "text",
                "output": "404 Not Found",
            },
        },
    )
    assert success.success is True and success.output_text == "404 Not Found"
    failed = normalize_tool_result(
        _envelope("fetch_url", {"url": "http://example.com"}),
        {
            "success": False,
            "status": "failed",
            "error": "Unable to fetch http://example.com",
            "error_code": "request_timeout",
            "data": {"url": "http://example.com", "error_code": "request_timeout"},
        },
    )
    assert failed.success is False and failed.status == "failed"
    assert failed.error_code == "request_timeout"
    serialized = json.dumps(failed.data, ensure_ascii=False)
    assert "SourceRecord" not in serialized and "source_required" not in serialized


def test_write_content_integrity_and_source_reference_remain() -> None:
    content = "Visible\n{\"task_state\": {\"secret\": true}}\nAlso visible"
    cleaned = _sanitize_write_file_content(content)
    assert cleaned == content
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        path = Path(temporary) / "payload.txt"
        path.write_text("externalized payload", encoding="utf-8")
        resolved = resolve_source_file_metadata(path, operation="read")
        assert resolved.ok is True
        assert resolved.ref == str(path.resolve())
        assert resolved.sha256 and resolved.bytes == len(b"externalized payload")


def main() -> None:
    test_removed_modules_and_imports()
    test_prompts_and_evals_have_no_source_authority()
    test_web_search_has_no_source_classification()
    test_web_observations_remain_execution_facts()
    test_write_content_integrity_and_source_reference_remain()
    print("smoke_source_architecture_removal ok")


if __name__ == "__main__":
    main()
