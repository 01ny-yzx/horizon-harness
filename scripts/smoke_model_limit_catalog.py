"""Offline smoke checks for the generated model token-limit catalog."""

from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.base import LLMConfig
from providers.capabilities import resolve_capability_profile
from providers.factory import config_from_settings
from providers.model_catalog import resolve_model_limit, snapshot_metadata
from core.context_budget import apply_context_budget
from scripts.update_model_catalog import build_snapshot


def test_deterministic_fixture_generation() -> None:
    fixture = {
        "openai": {"models": {"gpt-smoke": {"name": "GPT Smoke", "limit": {"context": 8192, "input": 7000, "output": 1024}}}},
        "anthropic": {"models": {"claude-smoke": {"name": "Claude Smoke", "limit": {"context": 16384, "output": 2048}}}},
        "invalid": {"models": {"missing-output": {"limit": {"context": 1000}}}},
    }
    raw = json.dumps(fixture, sort_keys=True).encode()
    assert build_snapshot(raw, source="fixture") == build_snapshot(raw, source="fixture")
    snapshot = build_snapshot(raw, source="fixture")
    assert snapshot["model_count"] == 2 and snapshot["provider_count"] == 2


def test_snapshot_coverage_and_positive_limits() -> None:
    snapshot_path = ROOT / "providers/model_catalog_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    assert snapshot["model_count"] >= 1000 and snapshot["provider_count"] >= 50
    models = snapshot["models"]
    assert all(item["max_context_tokens"] > 0 and item["max_output_tokens"] > 0 for item in models)
    required = ("openai", "anthropic", "google", "deepseek", "alibaba", "mistral", "xai", "amazon", "cohere", "minimax", "moonshot", "zhipu", "baidu", "microsoft", "nvidia", "groq", "cerebras", "openrouter")
    searchable = [f"{item['provider_id']}/{item['model_id']}".lower() for item in models]
    assert all(any(provider in value for value in searchable) for provider in required)
    metadata = snapshot_metadata()
    assert metadata["snapshot_sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert metadata["upstream_sha256"] == snapshot["upstream_sha256"]
    assert metadata["snapshot_sha256"] != metadata["upstream_sha256"]


def test_automatic_provider_and_model_resolution() -> None:
    cases = (
        ("https://api.openai.com/v1", "gpt-4.1-mini", "openai/gpt-4.1-mini"),
        ("https://api.anthropic.com/v1", "claude-haiku-4-5", "anthropic/claude-haiku-4-5"),
        ("https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash", "google/gemini-2.5-flash"),
        ("https://api.deepseek.com", "deepseek-flash", "deepseek/deepseek-flash"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen3.5-plus", "alibaba/qwen3.5-plus"),
        ("https://api.mistral.ai/v1", "mistral-large-latest", "mistral/mistral-large-latest"),
        ("https://api.x.ai/v1", "grok-4.3", "xai/grok-4.3"),
        ("https://openrouter.ai/api/v1", "deepseek/deepseek-chat", "openrouter/deepseek/deepseek-chat"),
    )
    for base_url, model, expected in cases:
        resolved = resolve_model_limit("openai_compatible", model, base_url=base_url)
        assert resolved and resolved.canonical_id == expected

    deepseek_flash = resolve_model_limit(
        "openai_compatible",
        "deepseek-flash",
        base_url="https://api.deepseek.com",
    )
    assert deepseek_flash.canonical_id == "deepseek/deepseek-flash"
    assert deepseek_flash.provider_id == "deepseek"
    assert deepseek_flash.max_context_tokens == 1_000_000
    assert deepseek_flash.max_input_tokens is None
    assert deepseek_flash.max_output_tokens == 384_000
    assert not deepseek_flash.ambiguous
    assert deepseek_flash.resolution_reason == "endpoint_provider_exact_model"

    alias = resolve_model_limit("openai_compatible", "mimo-v2.5-pro")
    assert alias and alias.canonical_id == "xiaomi/mimo-v2.5-pro" and alias.source == "catalog_alias"
    expected_aliases = {
        "qwen3.5-flash": "alibaba-cn/qwen3.5-flash",
        "qwen3.5-plus": "alibaba/qwen3.5-plus",
        "mimo-v2.5-pro": "xiaomi/mimo-v2.5-pro",
    }
    for model, canonical_id in expected_aliases.items():
        resolved = resolve_model_limit("openai_compatible", model)
        assert resolved and resolved.canonical_id == canonical_id

    snapshot = json.loads((ROOT / "providers/model_catalog_snapshot.json").read_text())
    counts = Counter(item["model_id"] for item in snapshot["models"])
    unique = next(item for item in snapshot["models"] if counts[item["model_id"]] == 1)
    resolved_unique = resolve_model_limit("proxy", unique["model_id"], base_url="https://unknown.invalid/v1")
    assert resolved_unique and resolved_unique.canonical_id == unique["canonical_id"]
    unresolved = resolve_model_limit("proxy", "glm-5", base_url="https://unknown.invalid/v1")
    assert unresolved.resolution_reason == "unresolved" and unresolved.max_context_tokens is None
    unresolved_mimo = resolve_model_limit("openai_compatible", "mimo-v2.5")
    assert unresolved_mimo.resolution_reason == "unresolved" and unresolved_mimo.max_context_tokens is None


def test_deepseek_flash_capabilities_and_factory() -> None:
    profile = resolve_capability_profile(
        LLMConfig(
            provider="openai_compatible",
            model="deepseek-flash",
            base_url="https://api.deepseek.com",
        )
    )
    assert profile.effective_preset == "deepseek_reasoning"
    assert profile.preset_source == "model"
    assert profile.resolved_provider_id == "deepseek"
    assert profile.resolved_model_id == "deepseek/deepseek-flash"
    assert not profile.model_limit_ambiguous
    assert profile.capabilities.supports_reasoning is True
    assert profile.capabilities.requires_reasoning_echo is True
    assert profile.capabilities.supports_disable_reasoning is True
    assert profile.capabilities.max_context_tokens == 1_000_000
    assert profile.capabilities.max_input_tokens is None
    assert profile.capabilities.max_output_tokens == 384_000

    settings = SimpleNamespace(
        llm_provider="openai_compatible",
        llm_model="deepseek-flash",
        llm_base_url="https://api.deepseek.com",
        llm_api_key="test",
        llm_temperature=0.2,
        llm_timeout=60,
        llm_reasoning_mode="auto",
        llm_extra_body=None,
        llm_capability_preset="",
        llm_capabilities_json=None,
    )
    config = config_from_settings(settings)
    assert config.model == "deepseek-flash"
    assert config.capability_preset_effective == "deepseek_reasoning"
    assert config.resolved_provider_id == "deepseek"
    assert config.resolved_model_id == "deepseek/deepseek-flash"
    assert config.capabilities is not None
    assert config.capabilities.max_context_tokens == 1_000_000
    assert config.capabilities.max_input_tokens is None
    assert config.capabilities.max_output_tokens == 384_000


def test_field_overrides_and_input_budget() -> None:
    alias = resolve_model_limit("openai_compatible", "mimo-v2.5-pro")
    assert alias is not None

    context_only = resolve_capability_profile(
        LLMConfig(provider="openai_compatible", model="mimo-v2.5-pro"),
        {"max_context_tokens": alias.max_context_tokens + 1000},
    )
    assert context_only.capabilities.max_context_tokens == alias.max_context_tokens + 1000
    assert context_only.capabilities.max_output_tokens == alias.max_output_tokens
    assert context_only.model_limit_source == "manual_override"

    output_only = resolve_capability_profile(
        LLMConfig(provider="openai_compatible", model="mimo-v2.5-pro"),
        {"max_output_tokens": 3333},
    )
    assert output_only.capabilities.max_context_tokens == alias.max_context_tokens
    assert output_only.capabilities.max_output_tokens == 3333

    input_only = resolve_capability_profile(
        LLMConfig(provider="openai_compatible", model="mimo-v2.5-pro"),
        {"max_input_tokens": alias.max_context_tokens - 1},
    )
    assert input_only.capabilities.max_input_tokens == alias.max_context_tokens - 1
    assert input_only.capabilities.max_context_tokens == alias.max_context_tokens

    _, decision = apply_context_budget(
        [{"role": "user", "content": "small"}],
        tools=[],
        runtime_lane="chat",
        task_state=SimpleNamespace(metadata={}),
        model_context_tokens=10000,
        model_input_tokens=6000,
        reserved_output_tokens=1000,
    )
    assert decision.model_input_tokens == 6000 and decision.usable_input_tokens == 5000
    _, no_input_decision = apply_context_budget(
        [{"role": "user", "content": "small"}],
        tools=[],
        runtime_lane="chat",
        task_state=SimpleNamespace(metadata={}),
        model_context_tokens=10000,
        reserved_output_tokens=1000,
    )
    assert no_input_decision.model_input_tokens is None and no_input_decision.usable_input_tokens == 9000


def test_unknown_model_fails_factory_resolution() -> None:
    settings = SimpleNamespace(
        llm_provider="openai_compatible",
        llm_model="unknown-model-with-no-exact-alias",
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_temperature=0.2,
        llm_timeout=10,
        llm_reasoning_mode="auto",
        llm_extra_body=None,
        llm_capability_preset="",
        llm_capabilities_json=None,
    )
    try:
        config_from_settings(settings)
    except RuntimeError as exc:
        assert "Unable to resolve token limits" in str(exc)
        assert "max_input_tokens" in str(exc)
    else:
        raise AssertionError("unknown model must fail closed")

    assert "LLM_MODEL_CATALOG_ID" not in (ROOT / "config/settings.py").read_text()
    assert "LLM_MODEL_CATALOG_ID" not in (ROOT / ".env.example").read_text()


def main() -> None:
    test_deterministic_fixture_generation()
    test_snapshot_coverage_and_positive_limits()
    test_automatic_provider_and_model_resolution()
    test_deepseek_flash_capabilities_and_factory()
    test_field_overrides_and_input_budget()
    test_unknown_model_fails_factory_resolution()
    print("smoke_model_limit_catalog ok")


if __name__ == "__main__":
    main()
