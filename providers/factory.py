"""Factory for runtime LLM provider creation."""

from __future__ import annotations

from providers.base import LLMConfig, LLMProvider
from providers.capabilities import resolve_capability_profile
from providers.registry import get_provider_builder, resolve_provider_name


def config_from_settings(settings: object) -> LLMConfig:
    provider = str(getattr(settings, "llm_provider", "openai_compatible") or "openai_compatible")
    resolved_provider = resolve_provider_name(provider)
    config = LLMConfig(
        provider=resolved_provider,
        model=str(getattr(settings, "llm_model", "") or ""),
        base_url=str(getattr(settings, "llm_base_url", "") or ""),
        api_key=str(getattr(settings, "llm_api_key", "") or ""),
        temperature=float(getattr(settings, "llm_temperature", 0.2)),
        timeout=float(getattr(settings, "llm_timeout", 60.0)),
        reasoning_mode=str(getattr(settings, "llm_reasoning_mode", "auto") or "auto"),
        extra_body=getattr(settings, "llm_extra_body", None),
        capability_preset=str(getattr(settings, "llm_capability_preset", "") or "") or None,
    )
    resolution = resolve_capability_profile(config, getattr(settings, "llm_capabilities_json", None))
    if resolution.capabilities.max_context_tokens is None:
        raise RuntimeError(
            f"Unable to resolve token limits for base_url={config.base_url}, model={config.model}. "
            "Configure max_context_tokens, max_input_tokens and max_output_tokens in LLM_CAPABILITIES_JSON."
        )
    return LLMConfig(
        **{
            **config.__dict__,
            "capabilities": resolution.capabilities,
            "capability_preset_requested": resolution.requested_preset,
            "capability_preset_effective": resolution.effective_preset,
            "capability_preset_source": resolution.preset_source,
            "model_limit_source": resolution.model_limit_source,
            "resolved_provider_id": resolution.resolved_provider_id,
            "resolved_model_id": resolution.resolved_model_id,
            "model_limit_status": resolution.model_limit_status,
            "model_catalog_snapshot_sha256": resolution.model_catalog_snapshot_sha256,
            "model_catalog_upstream_sha256": resolution.model_catalog_upstream_sha256,
            "endpoint_provider_candidates": resolution.endpoint_provider_candidates,
            "model_limit_candidate_models": resolution.model_limit_candidate_models,
            "model_limit_resolution_reason": resolution.model_limit_resolution_reason,
            "model_limit_ambiguous": resolution.model_limit_ambiguous,
        }
    )


def create_llm_provider(settings: object) -> LLMProvider:
    config = config_from_settings(settings)
    builder = get_provider_builder(str(getattr(settings, "llm_provider", config.provider)))
    return builder(config)
