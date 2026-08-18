"""Common LLM provider protocol and configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.unicode_safety import sanitize_unicode

REASONING_METADATA_KEYS = ("reasoning_content", "reasoning", "thinking")
PROVIDER_RESPONSE_METADATA_KEYS = (*REASONING_METADATA_KEYS, "finish_reason", "provider_finish_reason")


def extract_provider_metadata(message: Any) -> dict[str, Any]:
    """Extract provider-only reasoning metadata from an assistant message."""

    metadata: dict[str, Any] = {}
    dumped: dict[str, Any] = {}
    if hasattr(message, "model_dump"):
        try:
            value = message.model_dump()
        except Exception:
            value = {}
        if isinstance(value, dict):
            dumped = value

    sources: list[dict[str, Any]] = []
    if dumped:
        sources.append(dumped)
    if isinstance(message, dict):
        sources.append(message)

    for source in sources:
        for key in PROVIDER_RESPONSE_METADATA_KEYS:
            value = source.get(key)
            if value:
                metadata[key] = value

    for key in PROVIDER_RESPONSE_METADATA_KEYS:
        if key in metadata:
            continue
        value = getattr(message, key, None)
        if value:
            metadata[key] = value

    return sanitize_unicode(metadata)


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


@dataclass(frozen=True)
class LLMUsage:
    """Provider-reported token usage, without estimating unavailable fields."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    available: bool = False

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name)))

    def safe_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


@dataclass(frozen=True)
class LLMChatResult:
    """Complete safe result of one Provider turn."""

    message: Any
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str = ""
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_metadata", sanitize_unicode(dict(self.provider_metadata or {})))
        object.__setattr__(self, "finish_reason", str(self.finish_reason or ""))


def coerce_llm_chat_result(value: Any) -> LLMChatResult:
    """Wrap legacy bare messages without inventing usage data."""

    if isinstance(value, LLMChatResult):
        return value
    metadata = extract_provider_metadata(value)
    finish_reason = str(
        metadata.get("provider_finish_reason")
        or metadata.get("finish_reason")
        or ""
    )
    return LLMChatResult(
        message=value,
        finish_reason=finish_reason,
        provider_metadata=metadata,
    )


def call_llm_chat_result(
    llm: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    options: "LLMCallOptions | None" = None,
) -> LLMChatResult:
    """Call a modern provider result API while preserving legacy chat compatibility."""

    chat_result = getattr(llm, "chat_result", None)
    if callable(chat_result):
        return coerce_llm_chat_result(
            chat_result(messages=messages, tools=tools, options=options)
        )
    return coerce_llm_chat_result(
        llm.chat(messages=messages, tools=tools, options=options)
    )


@dataclass(frozen=True)
class LLMCallOptions:
    """Per-call LLM request options for one runtime stage."""

    stage: str = ""
    max_tokens: int | None = None
    temperature: float | None = None
    timeout: float | None = None
    disable_reasoning: bool | None = None
    response_format: dict[str, Any] | None = None

    def safe_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


@dataclass(frozen=True)
class LLMCapabilities:
    """Safe model capability profile used by providers and status endpoints."""

    supports_tools: bool = True
    supports_reasoning: bool = False
    requires_reasoning_echo: bool = False
    supports_disable_reasoning: bool = False
    supports_extra_body: bool = False
    supports_json_mode: bool | None = None
    supports_streaming: bool | None = None
    max_context_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    notes: str | None = None

    def safe_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EffectiveModelLimits:
    """Raw catalog limits plus the usable budget for one concrete LLM call."""

    raw_context_tokens: int
    raw_input_tokens: int | None
    raw_output_tokens: int | None
    effective_input_tokens: int
    requested_output_tokens: int
    reserved_output_tokens: int
    usable_input_tokens: int
    correction_reason: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


@dataclass(frozen=True)
class LLMConfig:
    """Runtime configuration shared by LLM providers."""

    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.2
    timeout: float = 60.0
    reasoning_mode: str = "auto"
    extra_body: dict[str, Any] | None = None
    capabilities: LLMCapabilities | None = None
    capability_preset: str | None = None
    capability_preset_requested: str | None = None
    capability_preset_effective: str | None = None
    capability_preset_source: str = "default"
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

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key.strip())

    def safe_dict(self) -> dict[str, Any]:
        """Return status-safe config without secret values."""

        data = asdict(self)
        data.pop("api_key", None)
        data.pop("extra_body", None)
        data["capabilities"] = self.capabilities.safe_dict() if self.capabilities else None
        data["capability_preset"] = self.capability_preset
        data["capability_preset_requested"] = self.capability_preset_requested
        data["capability_preset_effective"] = self.capability_preset_effective
        data["capability_preset_source"] = self.capability_preset_source
        data["api_key_configured"] = self.api_key_configured
        data["extra_body_configured"] = bool(self.extra_body)
        return data


@runtime_checkable
class LLMProvider(Protocol):
    """Provider interface consumed by AgentLoop."""

    name: str
    config: LLMConfig

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> Any:
        """Return an assistant message compatible with OpenAI chat completions."""

    def chat_result(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: LLMCallOptions | None = None,
    ) -> LLMChatResult:
        """Return an assistant message together with safe Provider turn metadata."""

    def get_status(self) -> dict[str, Any]:
        """Return safe provider status without API keys."""
