from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.context_budget import apply_context_budget
from providers.base import LLMCapabilities
from providers.capabilities import resolve_effective_model_limits
from providers.factory import create_llm_provider


def _settings(base_url: str, model: str) -> SimpleNamespace:
    return SimpleNamespace(
        llm_provider="openai_compatible", llm_model=model, llm_base_url=base_url,
        llm_api_key="smoke-key", llm_temperature=0.2, llm_timeout=10,
        llm_reasoning_mode="auto", llm_extra_body=None, llm_capability_preset="",
        llm_capabilities_json=None,
    )


def main() -> None:
    equal = LLMCapabilities(max_context_tokens=8192, max_input_tokens=9000, max_output_tokens=8192)
    limits = resolve_effective_model_limits(equal, 512)
    assert limits.raw_output_tokens == 8192 and limits.effective_input_tokens == 8192
    assert limits.requested_output_tokens == 512 and limits.reserved_output_tokens == 512
    assert limits.usable_input_tokens == 7680 and "catalog_input_limit_ignored" in limits.correction_reason

    defaulted = resolve_effective_model_limits(LLMCapabilities(max_context_tokens=8192, max_output_tokens=8192), None)
    assert defaulted.requested_output_tokens == 1024 and defaulted.usable_input_tokens == 7168
    assert defaulted.requested_output_tokens != defaulted.raw_output_tokens

    try:
        resolve_effective_model_limits(LLMCapabilities(max_context_tokens=1024, max_output_tokens=4096), 1024)
    except RuntimeError as exc:
        assert "remain positive" in str(exc)
    else:
        raise AssertionError("exhausted input budget must fail")

    messages = [{"role": "system", "content": "stable"}, {"role": "user", "content": "hello"}]
    kept, decision = apply_context_budget(
        messages, tools=[], runtime_lane="chat", task_state=SimpleNamespace(metadata={}),
        model_context_tokens=8192, model_input_tokens=9000, model_output_tokens=8192,
        requested_output_tokens=256,
    )
    assert kept == messages and decision.effective_model_input_tokens == 8192
    assert decision.requested_output_tokens == 256 and decision.usable_input_tokens == 7936
    assert decision.model_limit_correction_reason == "catalog_input_limit_ignored"

    mistral = create_llm_provider(_settings("https://api.mistral.ai/v1", "mistral-large-latest"))
    kimi = create_llm_provider(_settings("https://api.moonshot.ai/v1", "kimi-k2.5"))
    assert mistral.capabilities.max_context_tokens == mistral.capabilities.max_output_tokens == 262144
    assert kimi.capabilities.max_context_tokens == kimi.capabilities.max_output_tokens == 262144
    print("smoke_effective_model_limits ok")


if __name__ == "__main__":
    main()
