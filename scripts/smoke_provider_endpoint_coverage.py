"""Smoke checks for exact Provider endpoint and model resolution."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.model_catalog import merged_provider_endpoints, resolve_model_limit, resolve_provider_candidates, resolve_provider_id


def test_endpoint_resolution() -> None:
    cases = {
        "https://api.openai.com:443/v1?x=1": "openai",
        "https://api.anthropic.com/v1": "anthropic",
        "https://generativelanguage.googleapis.com/v1beta": "google",
        "https://us-central1-aiplatform.googleapis.com/v1": "google-vertex",
        "https://api.deepseek.com/v1": "deepseek",
        "https://dashscope.aliyuncs.com/api/v1": "alibaba-cn",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1": "alibaba",
        "https://api.fireworks.ai/inference/v1": "fireworks-ai",
        "https://api.moonshot.ai/v1": "moonshotai",
        "https://api.minimax.io/v1": "minimax",
        "https://router.huggingface.co/v1": "huggingface",
        "https://api.mistral.ai/v1": "mistral",
        "https://api.x.ai/v1": "xai",
        "https://openrouter.ai/api/v1": "openrouter",
        "https://api.xiaomimimo.com/v1": "xiaomi",
        "https://api.groq.com/openai/v1": "groq",
        "https://api.cerebras.ai/v1": "cerebras",
        "https://api.perplexity.ai/v1": "perplexity",
        "https://api.together.xyz/v1": "togetherai",
        "https://api.deepinfra.com/v1/openai": "deepinfra",
        "https://api.cohere.ai/v2": "cohere",
        "https://resource.openai.azure.com/openai/deployments/x": "azure",
        "https://resource.cognitiveservices.azure.com/openai/v1": "azure",
    }
    for endpoint, provider in cases.items():
        candidates = resolve_provider_candidates(endpoint)
        assert provider in candidates, (endpoint, provider, candidates)
        if len(candidates) == 1:
            assert resolve_provider_id(endpoint) == provider
        else:
            assert resolve_provider_id(endpoint) == ""
    for spoofed in (
        "https://api.groq.com.evil.test/v1",
        "https://openai.azure.com.evil.test/v1",
        "https://evil-aiplatform.googleapis.com.evil.test/v1",
        "https://api.fireworks.ai.evil.test/v1",
        "https://router.huggingface.co.evil.test/v1",
    ):
        assert resolve_provider_candidates(spoofed) == ()


def test_merged_endpoint_catalog() -> None:
    first = merged_provider_endpoints()
    second = merged_provider_endpoints()
    assert first == second
    assert [item["provider_id"] for item in first] == sorted(item["provider_id"] for item in first)
    by_provider = {item["provider_id"]: item for item in first}
    assert "api.fireworks.ai" in by_provider["fireworks-ai"]["exact_hosts"]
    assert "models.dev" in by_provider["fireworks-ai"]["sources"]
    assert "api.groq.com" in by_provider["groq"]["exact_hosts"]
    assert by_provider["groq"].get("local_verification")
    assert "dashscope.aliyuncs.com" not in by_provider["alibaba"]["exact_hosts"]
    assert "dashscope.aliyuncs.com" in by_provider["alibaba-cn"]["exact_hosts"]


def test_provider_scoped_model_resolution() -> None:
    cases = (
        ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant", "groq/llama-3.1-8b-instant"),
        ("https://api.cerebras.ai/v1", "gpt-oss-120b", "cerebras/gpt-oss-120b"),
        ("https://api.perplexity.ai/v1", "sonar", "perplexity/sonar"),
        ("https://api.together.xyz/v1", "Qwen/Qwen3.5-9B", "togetherai/Qwen/Qwen3.5-9B"),
        ("https://api.deepinfra.com/v1/openai", "Qwen/Qwen3.5-9B", "deepinfra/Qwen/Qwen3.5-9B"),
        ("https://api.cohere.ai/v2", "command-a-03-2025", "cohere/command-a-03-2025"),
    )
    for endpoint, model, canonical in cases:
        resolved = resolve_model_limit("openai_compatible", model, base_url=endpoint)
        assert resolved and resolved.canonical_id == canonical
    unresolved = resolve_model_limit("proxy", "gpt-oss-120b", base_url="https://unknown.invalid/v1")
    assert unresolved.resolution_reason == "unresolved" and unresolved.max_context_tokens is None


def main() -> None:
    test_merged_endpoint_catalog()
    test_endpoint_resolution()
    test_provider_scoped_model_resolution()
    print("smoke_provider_endpoint_coverage ok")


if __name__ == "__main__":
    main()
