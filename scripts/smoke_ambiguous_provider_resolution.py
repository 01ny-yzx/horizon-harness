"""Smoke checks for endpoint Provider candidates and fail-closed model limits."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime_metrics import RuntimeMetrics
from core.trace import AgentTrace
from providers.base import LLMCapabilities
from providers.model_catalog import resolve_model_limit, resolve_provider_candidates, resolve_provider_id


def test_provider_candidates_and_limits() -> None:
    perplexity = resolve_provider_candidates("https://api.perplexity.ai/v1")
    assert perplexity == ("perplexity", "perplexity-agent")
    assert resolve_provider_id("https://api.perplexity.ai/v1") == ""

    sonar = resolve_model_limit("openai_compatible", "sonar", base_url="https://api.perplexity.ai/v1")
    assert sonar.canonical_id == "perplexity/sonar"
    assert sonar.candidate_provider_ids == perplexity
    assert sonar.resolution_reason == "endpoint_provider_exact_model"

    shared = resolve_model_limit("openai_compatible", "glm-5.2", base_url="https://opencode.ai/v1")
    assert shared.resolution_reason == "shared_identical_limits" and not shared.ambiguous
    assert shared.provider_id == "" and len(shared.candidate_model_ids) == 2
    assert shared.max_context_tokens == 1_000_000 and shared.max_output_tokens == 131_072

    ambiguous = resolve_model_limit("openai_compatible", "glm-5", base_url="https://opencode.ai/v1")
    assert ambiguous.ambiguous and ambiguous.resolution_reason == "ambiguous_provider_model"
    assert ambiguous.max_context_tokens is None and ambiguous.max_output_tokens is None

    opencode_mimo = resolve_model_limit("openai_compatible", "mimo-v2.5-pro", base_url="https://opencode.ai/v1")
    assert opencode_mimo.provider_id == "opencode-go"
    assert opencode_mimo.canonical_id != "xiaomi/mimo-v2.5-pro"

    zhipu = resolve_model_limit("openai_compatible", "glm-4.5-air", base_url="https://open.bigmodel.cn/v1")
    assert zhipu.resolution_reason == "endpoint_provider_exact_model"
    assert zhipu.candidate_provider_ids == ("zhipuai", "zhipuai-coding-plan")
    assert zhipu.canonical_id == "zhipuai/glm-4.5-air"

    alias_conflict = resolve_model_limit("openai_compatible", "deepseek-chat", base_url="https://api.openai.com/v1")
    assert alias_conflict.resolution_reason == "unresolved" and not alias_conflict.canonical_id

    unique = resolve_model_limit("proxy", "deepseek-chat", base_url="https://unknown.invalid/v1")
    assert unique.resolution_reason == "unique_global_model_id" and unique.canonical_id == "nano-gpt/deepseek-chat"

    canonical_conflict = resolve_model_limit("openai_compatible", "xiaomi/mimo-v2.5-pro", base_url="https://opencode.ai/v1")
    assert canonical_conflict.ambiguous and canonical_conflict.resolution_reason == "ambiguous_provider_model"
    assert canonical_conflict.max_context_tokens is None


def test_vertex_hosts_and_trace_metadata() -> None:
    valid = (
        "us-central1-aiplatform.googleapis.com",
        "europe-west4-aiplatform.googleapis.com",
        "asia-northeast1-aiplatform.googleapis.com",
        "global-aiplatform.googleapis.com",
    )
    for host in valid:
        assert resolve_provider_candidates(f"https://{host}/v1") == ("google-vertex",)
    for host in (
        "aiplatform.googleapis.com.evil.com",
        "europe-west4-aiplatform.googleapis.com.evil.com",
        "evil-europe-west4-aiplatform.googleapis.com.attacker.net",
    ):
        assert resolve_provider_candidates(f"https://{host}/v1") == ()

    resolution = resolve_model_limit("openai_compatible", "glm-5.2", base_url="https://opencode.ai/v1")
    config = SimpleNamespace(
        capabilities=LLMCapabilities(max_context_tokens=resolution.max_context_tokens, max_output_tokens=resolution.max_output_tokens),
        model_limit_source=resolution.source,
        resolved_provider_id=resolution.provider_id,
        resolved_model_id=resolution.canonical_id,
        model_catalog_snapshot_sha256=resolution.snapshot_sha256,
        model_catalog_upstream_sha256=resolution.upstream_sha256,
        endpoint_provider_candidates=resolution.candidate_provider_ids,
        model_limit_candidate_models=resolution.candidate_model_ids,
        model_limit_resolution_reason=resolution.resolution_reason,
        model_limit_ambiguous=resolution.ambiguous,
    )
    metrics = RuntimeMetrics()
    metrics.record_model_limits(config)
    summary = metrics.summary()
    assert summary["endpoint_provider_candidates"] == ["opencode", "opencode-go"]
    assert summary["model_limit_resolution_reason"] == "shared_identical_limits"
    trace = AgentTrace("task", "goal", "smoke")
    trace.add_event(0, "runtime_metrics", "model limits", data=summary)
    event = trace.to_dict()["events"][0]
    assert event["data"]["endpoint_provider_candidates"] == ["opencode", "opencode-go"]
    assert event["data"]["model_limit_resolution_reason"] == "shared_identical_limits"


def main() -> None:
    test_provider_candidates_and_limits()
    test_vertex_hosts_and_trace_metadata()
    print("smoke_ambiguous_provider_resolution ok")


if __name__ == "__main__":
    main()
