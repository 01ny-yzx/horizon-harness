"""Resolve per-call LLM request profiles for runtime stages."""

from __future__ import annotations

import os
from typing import Any

from providers.base import LLMCallOptions


SUPPORTED_STAGES = {
    "initial_agent_turn",
    "tool_call",
    "agent_continuation",
    "final_answer",
    "single_file_read_final_answer",
    "single_file_read_agent_continuation",
}


def resolve_llm_call_options(stage: str, settings: Any) -> LLMCallOptions:
    """Return per-call options for one known LLM stage."""

    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage not in SUPPORTED_STAGES:
        normalized_stage = ""
    timeout = _settings_timeout(settings)

    if normalized_stage == "initial_agent_turn":
        defaults = LLMCallOptions(
            stage="initial_agent_turn",
            max_tokens=1024,
            temperature=0.0,
            timeout=min(timeout, 30.0),
            disable_reasoning=True,
            response_format=None,
        )
    elif normalized_stage in {"tool_call", "agent_continuation"}:
        defaults = LLMCallOptions(
            stage=normalized_stage,
            max_tokens=1536,
            temperature=0.0,
            timeout=min(timeout, 45.0),
            disable_reasoning=True,
        )
    elif normalized_stage == "final_answer":
        defaults = LLMCallOptions(
            stage="final_answer",
            max_tokens=1024,
            temperature=0.2,
            timeout=min(timeout, 45.0),
            disable_reasoning=True,
        )
    elif normalized_stage in {"single_file_read_final_answer", "single_file_read_agent_continuation"}:
        defaults = LLMCallOptions(
            stage=normalized_stage,
            max_tokens=1024,
            temperature=0.0,
            timeout=min(timeout, 30.0),
            disable_reasoning=True,
        )
    else:
        defaults = LLMCallOptions(stage=normalized_stage)

    return _apply_env_overrides(defaults)


def _apply_env_overrides(defaults: LLMCallOptions) -> LLMCallOptions:
    prefix = _env_prefix(defaults.stage)
    if not prefix:
        return defaults
    return LLMCallOptions(
        stage=defaults.stage,
        max_tokens=_optional_int(f"{prefix}_MAX_TOKENS", defaults.max_tokens),
        temperature=_optional_float(f"{prefix}_TEMPERATURE", defaults.temperature),
        timeout=_optional_float(f"{prefix}_TIMEOUT", defaults.timeout),
        disable_reasoning=_optional_bool(f"{prefix}_DISABLE_REASONING", defaults.disable_reasoning),
        response_format=defaults.response_format,
    )


def _env_prefix(stage: str) -> str:
    if stage == "initial_agent_turn":
        return "LLM_PROFILE_INITIAL_AGENT_TURN"
    if stage == "final_answer":
        return "LLM_PROFILE_FINAL_ANSWER"
    if stage in {"single_file_read_final_answer", "single_file_read_agent_continuation"}:
        return "LLM_PROFILE_SINGLE_FILE_READ_FINAL_ANSWER"
    if stage in {"tool_call", "agent_continuation"}:
        return f"LLM_PROFILE_{stage.upper()}"
    return ""


def _settings_timeout(settings: Any) -> float:
    try:
        return float(getattr(settings, "llm_timeout", 60.0) or 60.0)
    except (TypeError, ValueError):
        return 60.0


def _optional_int(name: str, default: int | None) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _optional_float(name: str, default: float | None) -> float | None:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _optional_bool(name: str, default: bool | None) -> bool | None:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    return default
