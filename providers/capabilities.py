"""LLM capability presets and resolver."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from providers.base import EffectiveModelLimits, LLMCapabilities, LLMConfig
from providers.model_catalog import resolve_model_limit


@dataclass(frozen=True)
class CapabilityResolution:
    """Resolved capability profile plus safe provenance metadata."""

    capabilities: LLMCapabilities
    requested_preset: str | None = None
    effective_preset: str | None = None
    preset_source: str = "default"
    model_limit_source: str = ""
    resolved_provider_id: str = ""
    resolved_model_id: str = ""
    model_limit_status: str = ""
    model_catalog_snapshot_sha256: str = ""
    model_catalog_upstream_sha256: str = ""
    endpoint_provider_candidates: tuple[str, ...] = ()
    model_limit_candidate_models: tuple[str, ...] = ()
    model_limit_resolution_reason: str = "unresolved"
    model_limit_ambiguous: bool = False


CAPABILITY_PRESETS: dict[str, LLMCapabilities] = {
    "mock": LLMCapabilities(
        supports_tools=True,
        supports_reasoning=False,
        requires_reasoning_echo=False,
        supports_disable_reasoning=False,
        supports_extra_body=False,
        max_context_tokens=32768,
        max_output_tokens=4096,
        notes="Offline mock provider for tests.",
    ),
    "openai_compatible": LLMCapabilities(
        supports_tools=True,
        supports_reasoning=False,
        requires_reasoning_echo=False,
        supports_disable_reasoning=False,
        supports_extra_body=True,
        notes="OpenAI-compatible is a protocol; reasoning support depends on the model.",
    ),
    "reasoning_echo": LLMCapabilities(
        supports_tools=True,
        supports_reasoning=True,
        requires_reasoning_echo=True,
        supports_disable_reasoning=True,
        supports_extra_body=True,
        notes="Generic reasoning model that requires provider metadata echo for tool calls.",
    ),
    "deepseek_reasoning": LLMCapabilities(
        supports_tools=True,
        supports_reasoning=True,
        requires_reasoning_echo=True,
        supports_disable_reasoning=True,
        supports_extra_body=True,
        notes="Preset example for DeepSeek reasoning models on OpenAI-compatible APIs.",
    ),
}

MODEL_CAPABILITY_RULES: tuple[dict[str, Any], ...] = (
    {
        "provider": "openai_compatible",
        "model_contains": ("deepseek-flash",),
        "preset": "deepseek_reasoning",
    },
    {
        "provider": "openai_compatible",
        "model_contains": ("deepseek-reasoner", "reasoner"),
        "preset": "reasoning_echo",
    },
)

PROVIDER_CAPABILITY_RULES: tuple[dict[str, Any], ...] = (
    {
        "provider": "mock",
        "preset": "mock",
    },
)

CAPABILITY_FIELD_NAMES = {field.name for field in fields(LLMCapabilities)}


def default_capabilities_for_provider(provider: str) -> LLMCapabilities:
    return CAPABILITY_PRESETS.get(provider, CAPABILITY_PRESETS["openai_compatible"])


def resolve_capabilities(config: LLMConfig, overrides: dict[str, Any] | None = None) -> LLMCapabilities:
    return resolve_capability_profile(config, overrides).capabilities


def resolve_capability_profile(config: LLMConfig, overrides: dict[str, Any] | None = None) -> CapabilityResolution:
    capabilities = default_capabilities_for_provider(config.provider)
    requested_preset = (config.capability_preset or "").strip() or None
    effective_preset: str | None = config.provider if config.provider in CAPABILITY_PRESETS else None
    preset_source = "provider" if effective_preset else "default"
    limit = resolve_model_limit(
        config.provider,
        config.model,
        base_url=config.base_url,
    )
    model_limit_source = ""
    if limit.max_context_tokens is not None and limit.max_output_tokens is not None and not limit.ambiguous:
        capabilities = replace(
            capabilities,
            max_context_tokens=limit.max_context_tokens,
            max_input_tokens=limit.max_input_tokens,
            max_output_tokens=limit.max_output_tokens,
        )
        model_limit_source = limit.source

    inferred_preset, inferred_source = _infer_preset(config)
    if inferred_preset:
        capabilities = merge_capabilities(capabilities, _preset_or_error(inferred_preset))
        effective_preset = inferred_preset
        preset_source = inferred_source

    if requested_preset:
        capabilities = merge_capabilities(capabilities, _preset_or_error(requested_preset))
        effective_preset = requested_preset
        preset_source = "explicit"

    if config.capabilities:
        capabilities = merge_capabilities(capabilities, config.capabilities)

    if overrides:
        capabilities = merge_capability_overrides(capabilities, overrides)
        preset_source = "override"
        if any(name in overrides for name in ("max_context_tokens", "max_input_tokens", "max_output_tokens")):
            model_limit_source = "manual_override"

    _validate_resolved_token_limits(capabilities)

    return CapabilityResolution(
        capabilities=capabilities,
        requested_preset=requested_preset,
        effective_preset=effective_preset,
        preset_source=preset_source,
        model_limit_source=model_limit_source,
        resolved_provider_id=limit.provider_id,
        resolved_model_id=limit.canonical_id,
        model_limit_status=limit.status,
        model_catalog_snapshot_sha256=limit.snapshot_sha256,
        model_catalog_upstream_sha256=limit.upstream_sha256,
        endpoint_provider_candidates=limit.candidate_provider_ids,
        model_limit_candidate_models=limit.candidate_model_ids,
        model_limit_resolution_reason=limit.resolution_reason,
        model_limit_ambiguous=limit.ambiguous,
    )


def merge_capabilities(base: LLMCapabilities, override: LLMCapabilities) -> LLMCapabilities:
    values = {}
    for field in fields(LLMCapabilities):
        value = getattr(override, field.name)
        if value is not None:
            values[field.name] = value
    return replace(base, **values)


def capabilities_from_dict(values: dict[str, Any]) -> LLMCapabilities:
    return LLMCapabilities(**_validated_capability_values(values))


def merge_capability_overrides(base: LLMCapabilities, values: dict[str, Any]) -> LLMCapabilities:
    return replace(base, **_validated_capability_values(values))


def _validated_capability_values(values: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(values) - CAPABILITY_FIELD_NAMES)
    if unknown:
        raise RuntimeError(f"LLM_CAPABILITIES_JSON contains unsupported field(s): {', '.join(unknown)}.")

    parsed: dict[str, Any] = {}
    for field in fields(LLMCapabilities):
        if field.name not in values:
            continue
        value = values[field.name]
        if field.name in {
            "supports_tools",
            "supports_reasoning",
            "requires_reasoning_echo",
            "supports_disable_reasoning",
            "supports_extra_body",
        }:
            if not isinstance(value, bool):
                raise RuntimeError(f"LLM_CAPABILITIES_JSON field {field.name} must be true or false.")
        elif field.name in {"supports_json_mode", "supports_streaming"}:
            if value is not None and not isinstance(value, bool):
                raise RuntimeError(f"LLM_CAPABILITIES_JSON field {field.name} must be true, false, or null.")
        elif field.name in {"max_context_tokens", "max_input_tokens", "max_output_tokens"}:
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                raise RuntimeError(f"LLM_CAPABILITIES_JSON field {field.name} must be a positive integer or null.")
        elif field.name == "notes":
            if value is not None and not isinstance(value, str):
                raise RuntimeError("LLM_CAPABILITIES_JSON field notes must be a string or null.")
        parsed[field.name] = value
    return parsed


def _validate_resolved_token_limits(capabilities: LLMCapabilities) -> None:
    context = capabilities.max_context_tokens
    if context is not None and (not isinstance(context, int) or isinstance(context, bool) or context <= 0):
        raise RuntimeError("max_context_tokens must be a positive integer.")


def resolve_effective_model_limits(
    capabilities: LLMCapabilities,
    requested_output_tokens: int | None,
    *,
    default_output_tokens: int = 1024,
) -> EffectiveModelLimits:
    """Calculate request-level limits without rewriting catalog metadata."""

    context = _positive_token_value(capabilities.max_context_tokens)
    if context is None:
        raise RuntimeError("Unable to resolve model token limits: max_context_tokens must be positive.")
    raw_input = _positive_token_value(capabilities.max_input_tokens)
    raw_output = _positive_token_value(capabilities.max_output_tokens)
    reasons: list[str] = []
    if raw_input is not None and raw_input <= context:
        effective_input = raw_input
    else:
        effective_input = context
        reasons.append("catalog_input_limit_ignored")

    requested = requested_output_tokens if requested_output_tokens is not None else default_output_tokens
    if not isinstance(requested, int) or isinstance(requested, bool) or requested <= 0:
        raise RuntimeError("requested_output_tokens must be a positive integer.")
    if raw_output is not None and requested > raw_output:
        requested = raw_output
        reasons.append("requested_output_capped_to_catalog")
    usable = effective_input - requested
    if usable <= 0:
        raise RuntimeError("Resolved model input budget must remain positive after reserving output tokens.")
    return EffectiveModelLimits(
        raw_context_tokens=context,
        raw_input_tokens=raw_input,
        raw_output_tokens=raw_output,
        effective_input_tokens=effective_input,
        requested_output_tokens=requested,
        reserved_output_tokens=requested,
        usable_input_tokens=usable,
        correction_reason=",".join(reasons),
    )


def _positive_token_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def reasoning_auto_policy(reasoning_mode: str, capabilities: LLMCapabilities, has_tools: bool = True) -> str:
    if reasoning_mode == "disabled":
        return "disabled_by_config"
    if reasoning_mode == "enabled":
        return "echo_if_available" if capabilities.requires_reasoning_echo else "normal_chat"
    if (
        has_tools
        and capabilities.supports_reasoning
        and capabilities.supports_disable_reasoning
        and capabilities.supports_extra_body
    ):
        return "auto_disable_for_tools"
    if capabilities.requires_reasoning_echo:
        return "echo_if_available"
    return "normal_chat"


def _preset_or_error(name: str) -> LLMCapabilities:
    preset = CAPABILITY_PRESETS.get(name)
    if not preset:
        raise RuntimeError(f"Unknown LLM capability preset: {name}.")
    return preset


def _infer_preset(config: LLMConfig) -> tuple[str | None, str]:
    model_preset = _model_preset(config.provider, config.model)
    if model_preset:
        return model_preset, "model"
    provider_preset = _provider_preset(config.provider, config.base_url)
    if provider_preset:
        return provider_preset, "provider"
    return None, "default"


def _model_preset(provider: str, model: str) -> str | None:
    lowered = model.strip().lower()
    for rule in MODEL_CAPABILITY_RULES:
        if rule.get("provider") and rule["provider"] != provider:
            continue
        markers = tuple(str(item).lower() for item in rule.get("model_contains", ()))
        if any(marker in lowered for marker in markers):
            return str(rule["preset"])
    return None


def _provider_preset(provider: str, base_url: str) -> str | None:
    lowered_base_url = base_url.strip().lower()
    for rule in PROVIDER_CAPABILITY_RULES:
        if rule.get("provider") and rule["provider"] != provider:
            continue
        markers = tuple(str(item).lower() for item in rule.get("base_url_contains", ()))
        if markers and not any(marker in lowered_base_url for marker in markers):
            continue
        return str(rule["preset"])
    return None
