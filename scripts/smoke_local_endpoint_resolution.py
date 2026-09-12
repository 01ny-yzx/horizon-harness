from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.factory import config_from_settings, create_llm_provider
from providers.model_catalog import resolve_model_limit, resolve_provider_candidates


def _settings(model: str, *, overrides: dict[str, int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        llm_provider="openai_compatible", llm_model=model, llm_base_url="http://127.0.0.1:1234/v1",
        llm_api_key="local", llm_temperature=0.2, llm_timeout=10, llm_reasoning_mode="auto",
        llm_extra_body=None, llm_capability_preset="", llm_capabilities_json=overrides,
    )


def main() -> None:
    endpoints = (
        "http://localhost:1234/v1", "http://127.0.0.1:8080/v1", "http://[::1]:1234/v1",
        "http://0.0.0.0:9999/v1", "http://test.localhost:4567/v1",
    )
    assert all(resolve_provider_candidates(endpoint) == () for endpoint in endpoints)
    assert resolve_provider_candidates("localhost") == ()
    assert resolve_provider_candidates("127.0.0.1") == ()

    snapshot = json.loads((ROOT / "providers/model_catalog_snapshot.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for item in snapshot["models"]:
        counts[item["model_id"]] = counts.get(item["model_id"], 0) + 1
    unique = next(item for item in snapshot["models"] if counts[item["model_id"]] == 1)
    unique_result = resolve_model_limit("openai_compatible", unique["model_id"], base_url=endpoints[0])
    assert unique_result.canonical_id == unique["canonical_id"] and unique_result.resolution_reason == "unique_global_model_id"

    alias = resolve_model_limit("openai_compatible", "mimo-v2.5-pro", base_url=endpoints[1])
    assert alias.canonical_id == "xiaomi/mimo-v2.5-pro" and alias.resolution_reason == "exact_alias"
    unresolved = resolve_model_limit("openai_compatible", "gpt-4o", base_url=endpoints[0])
    assert unresolved.resolution_reason == "unresolved" and unresolved.max_context_tokens is None
    try:
        config_from_settings(_settings("gpt-4o"))
    except RuntimeError as exc:
        assert "LLM_CAPABILITIES_JSON" in str(exc)
    else:
        raise AssertionError("ambiguous local model must fail without explicit limits")

    provider = create_llm_provider(
        _settings("private-local-model", overrides={"max_context_tokens": 8192, "max_output_tokens": 1024})
    )
    assert provider.config.base_url == "http://127.0.0.1:1234/v1"
    assert provider.capabilities.max_context_tokens == 8192 and not provider.config.endpoint_provider_candidates
    print("smoke_local_endpoint_resolution ok")


if __name__ == "__main__":
    main()
