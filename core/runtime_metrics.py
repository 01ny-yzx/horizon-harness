"""Runtime performance metrics for one agent task."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from core.unicode_safety import sanitize_unicode
from providers.base import LLMUsage


@dataclass(frozen=True)
class LLMCallAttemptHandle:
    """Opaque handle for one provider-call attempt."""

    stage: str
    attempt_index: int
    record_index: int
    started_at: float


@dataclass
class RuntimeMetrics:
    """Collect lightweight timing and prompt-size data for one task trace."""

    started_at: float = field(default_factory=time.perf_counter)
    total_ms: int = 0
    memory_prepare_ms: int = 0
    tool_scope_ms: int = 0
    tool_scope_budget_ms: int = 0
    context_budget_ms: int = 0
    auto_compact_ms: int = 0
    tool_call_llm_ms: int = 0
    llm_call_ms: list[int] = field(default_factory=list)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_total_tokens: int = 0
    llm_reasoning_tokens: int = 0
    llm_cache_read_tokens: int = 0
    llm_cache_write_tokens: int = 0
    llm_usage_available_count: int = 0
    llm_usage_unavailable_count: int = 0
    tokens_by_stage: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    finish_reason_counts_by_stage: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    llm_calls_by_stage: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    llm_call_count_by_stage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    prompt_chars_by_stage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_schema_chars_by_stage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    message_count_by_stage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    prompt_packs_by_stage: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    prompt_chars: list[int] = field(default_factory=list)
    message_count: list[int] = field(default_factory=list)
    tool_schema_count: list[int] = field(default_factory=list)
    tool_schema_chars: list[int] = field(default_factory=list)
    context_budget_original_chars: list[int] = field(default_factory=list)
    context_budget_final_chars: list[int] = field(default_factory=list)
    context_budget_saved_chars: list[int] = field(default_factory=list)
    context_budget_actions: list[str] = field(default_factory=list)
    context_budget_records: list[dict[str, Any]] = field(default_factory=list)
    model_limit_source: str = ""
    model_context_tokens: int = 0
    model_input_tokens: int = 0
    model_max_output_tokens: int = 0
    raw_model_context_tokens: int = 0
    raw_model_input_tokens: int = 0
    raw_model_output_tokens: int = 0
    effective_model_input_tokens: int = 0
    requested_output_tokens: int = 0
    reserved_output_tokens: int = 0
    usable_input_tokens: int = 0
    model_limit_correction_reason: str = ""
    resolved_provider_id: str = ""
    resolved_model_id: str = ""
    model_catalog_snapshot_sha256: str = ""
    model_catalog_upstream_sha256: str = ""
    endpoint_provider_candidates: list[str] = field(default_factory=list)
    model_limit_candidate_models: list[str] = field(default_factory=list)
    model_limit_resolution_reason: str = "unresolved"
    model_limit_ambiguous: bool = False
    tool_result_externalized: int = 0
    tool_result_ref_reused: int = 0
    idempotent_replay_ref_reused: int = 0
    tool_scope_budget_records: list[dict[str, Any]] = field(default_factory=list)
    tool_call_count: int = 0
    real_tool_execution_count: int = 0
    same_call_idempotent_replay_count: int = 0
    exact_tool_loop_stop_count: int = 0
    tool_execution_ms: int = 0
    tool_execution_records: list[dict[str, Any]] = field(default_factory=list)
    tool_execution_by_name_ms: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_execution_by_name_count: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_guard_fast_path_ms: int = 0
    tool_guard_fast_path_count: int = 0
    tool_guard_fast_path_allowed_count: int = 0
    tool_guard_fast_path_blocked_count: int = 0
    tool_guard_fast_path_records: list[dict[str, Any]] = field(default_factory=list)
    capability_surface: str = ""
    effective_capabilities: list[str] = field(default_factory=list)
    effective_tools: list[str] = field(default_factory=list)
    capability_surface_records: list[dict[str, Any]] = field(default_factory=list)
    final_answer_ms: int = 0
    pre_router_enabled: bool = False
    pre_router_route: str = ""
    pre_router_confidence: float = 0.0
    pre_router_kind: str = ""
    pre_router_reason: str = ""
    pre_router_required_capabilities: list[str] = field(default_factory=list)
    pre_router_requires_tools: bool = False
    pre_router_requires_file_read: bool = False
    pre_router_requires_document_load: bool = False
    pre_router_requires_file_write: bool = False
    pre_router_requires_command_exec: bool = False
    pre_router_requires_network: bool = False
    pre_router_requires_artifact_output: bool = False
    pre_router_requires_code_edit: bool = False
    pre_router_requires_database: bool = False
    pre_router_requires_browser: bool = False
    pre_router_requires_mcp: bool = False
    pre_router_requires_memory: bool = False
    pre_router_requires_git: bool = False
    pre_router_requires_validation: bool = False
    pre_router_requires_multi_step_planning: bool = False
    pre_router_timeout: bool = False
    pre_router_fallback_reason: str = ""
    pre_router_raw_response_chars: int = 0
    pre_router_normalized_route: str = ""
    pre_router_normalization_reason: str = ""
    initial_agent_turn_ms: int = 0
    initial_agent_turn_mode: str = ""
    initial_agent_turn_tool_count: int = 0
    initial_agent_turn_tool_names: list[str] = field(default_factory=list)
    initial_agent_turn_schema_chars: int = 0
    initial_agent_turn_direct_tool_execution: bool = False
    initial_agent_turn_attempt_count: int = 0
    initial_agent_turn_retry_count: int = 0
    initial_agent_turn_retry_reason: str = ""
    initial_agent_turn_terminal_failure: bool = False
    initial_agent_turn_failure_category: str = ""
    initial_agent_turn_failure_reason: str = ""
    initial_agent_turn_failure_status_code: int | None = None
    initial_agent_turn_attempt_consistent: bool = True
    initial_agent_turn_retry_delay_ms: int = 0
    initial_agent_turn_retry_delay_source: str = ""
    initial_agent_turn_retry_wait_applied: bool = False
    initial_agent_turn_retry_skipped_reason: str = ""
    agent_turn_history_message_count: int = 0
    agent_turn_history_chars: int = 0
    agent_turn_compacted: bool = False
    agent_turn_current_user_count: int = 0
    initial_tool_call_count: int = 0
    terminal_observation_count: int = 0
    final_observation_count: int = 0
    final_observation_call_ids: list[str] = field(default_factory=list)
    expected_call_ids: list[str] = field(default_factory=list)
    represented_call_ids: list[str] = field(default_factory=list)
    missing_call_ids: list[str] = field(default_factory=list)
    coverage_complete: bool = True
    observation_compaction_count: int = 0
    observation_original_chars_total: int = 0
    observation_compacted_chars_total: int = 0
    observation_saved_chars_total: int = 0
    observation_compaction_by_tool: dict[str, dict[str, int]] = field(default_factory=dict)
    max_model_observation_chars: int = 0
    model_observation_chars_by_step: list[dict[str, Any]] = field(default_factory=list)
    build_step_contract_enabled: bool = False
    build_step_contract_steps: int = 0
    build_step_contract_completed_steps: int = 0
    build_step_contract_current_step: int = 0
    build_step_contract_records: list[dict[str, Any]] = field(default_factory=list)
    agent_continuation_count: int = 0
    single_file_read_agent_continuation_count: int = 0
    exceptional_repair_count: int = 0
    direct_agent_prose_adopted_count: int = 0
    finalization_snapshot_count: int = 0
    isolated_finalization_pack_count: int = 0
    finalization_snapshot_reuse_count: int = 0
    finalization_context_incomplete_count: int = 0
    finalization_context_budget_failure_count: int = 0
    finalization_model_context_chars: int = 0
    finalization_trace_context_chars: int = 0
    isolated_terminal_synthesis_output_reject_count: int = 0
    isolated_terminal_synthesis_provider_error_count: int = 0
    isolated_terminal_synthesis_fallback_count: int = 0
    continuation_context_projection_chars: int = 0
    continuation_prompt_overhead_chars: int = 0
    permission_tool_universe_count: int = 0
    permission_tool_universe_schema_chars: int = 0

    def finish(self) -> None:
        """Freeze total elapsed time up to this point."""

        self.total_ms = _elapsed_ms(self.started_at)

    @contextmanager
    def measure(self, field_name: str) -> Iterator[None]:
        """Accumulate elapsed milliseconds into one integer field."""

        started = time.perf_counter()
        try:
            yield
        finally:
            current = int(getattr(self, field_name, 0) or 0)
            setattr(self, field_name, current + _elapsed_ms(started))

    def record_llm_call(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        elapsed_ms: int,
        stage: str = "",
        model: str = "",
        success: bool = True,
        error_code: str = "",
        options: Any | None = None,
    ) -> None:
        """Record one LLM call duration and request payload size."""

        elapsed = int(elapsed_ms or 0)
        prompt_chars = _messages_chars(messages)
        message_count = len(messages)
        tool_schema_chars = 0 if not tools else _json_chars(tools)
        normalized_stage = _normalize_stage(stage)
        self.llm_call_ms.append(elapsed)
        self.prompt_chars.append(prompt_chars)
        self.message_count.append(message_count)
        self.tool_schema_count.append(len(tools))
        self.tool_schema_chars.append(tool_schema_chars)
        self.llm_call_count_by_stage[normalized_stage] += 1
        self.prompt_chars_by_stage[normalized_stage] += prompt_chars
        self.tool_schema_chars_by_stage[normalized_stage] += tool_schema_chars
        self.message_count_by_stage[normalized_stage] += message_count
        if normalized_stage in {"tool_call", "agent_continuation", "single_file_read_agent_continuation"}:
            self.tool_call_llm_ms += elapsed
        if normalized_stage == "agent_continuation":
            self.agent_continuation_count += 1
        elif normalized_stage == "single_file_read_agent_continuation":
            self.single_file_read_agent_continuation_count += 1
        self.llm_calls_by_stage[normalized_stage].append(
            {
                "stage": normalized_stage,
                "duration_ms": elapsed,
                "prompt_chars": prompt_chars,
                "message_count": message_count,
                "tool_schema_chars": tool_schema_chars,
                "model": str(model or ""),
                "success": bool(success),
                "failure": not bool(success),
                "error_code": str(error_code or ""),
                **_llm_options_summary(options),
            }
        )

    def start_llm_attempt(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stage: str,
        model: str,
        options: Any | None,
        attempt_index: int,
    ) -> LLMCallAttemptHandle:
        """Record an LLM attempt immediately before crossing the provider boundary."""

        normalized_stage = _normalize_stage(stage)
        prompt_chars = _messages_chars(messages)
        message_count = len(messages)
        tool_schema_chars = 0 if not tools else _json_chars(tools)
        self.llm_call_count_by_stage[normalized_stage] += 1
        self.prompt_chars_by_stage[normalized_stage] += prompt_chars
        self.tool_schema_chars_by_stage[normalized_stage] += tool_schema_chars
        self.message_count_by_stage[normalized_stage] += message_count
        record = {
            "stage": normalized_stage,
            "attempt_index": int(attempt_index or 1),
            "status": "running",
            "duration_ms": 0,
            "prompt_chars": prompt_chars,
            "message_count": message_count,
            "tool_schema_count": len(tools),
            "tool_schema_chars": tool_schema_chars,
            "model": str(model or ""),
            "provider_success": False,
            "runtime_accepted": False,
            "error_code": "",
            "status_code": None,
            "retryable": False,
            "usage_available": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "finish_reason": "",
            "retry_delay_ms": 0,
            "retry_delay_source": "",
            "retry_wait_applied": False,
            "retry_skipped_reason": "",
            **_llm_options_summary(options),
        }
        records = self.llm_calls_by_stage[normalized_stage]
        records.append(record)
        return LLMCallAttemptHandle(
            stage=normalized_stage,
            attempt_index=int(attempt_index or 1),
            record_index=len(records) - 1,
            started_at=time.perf_counter(),
        )

    def finish_llm_attempt(
        self,
        handle: LLMCallAttemptHandle,
        *,
        provider_success: bool,
        runtime_accepted: bool,
        error_code: str = "",
        status_code: int | None = None,
        retryable: bool = False,
        provider_metadata: dict[str, Any] | None = None,
        usage: LLMUsage | None = None,
        finish_reason: str = "",
    ) -> dict[str, Any]:
        """Close one started LLM attempt without inferring outcome from later state."""

        records = self.llm_calls_by_stage.get(handle.stage, [])
        if handle.record_index >= len(records):
            return {}
        record = records[handle.record_index]
        duration_ms = _elapsed_ms(handle.started_at)
        safe_usage = usage if isinstance(usage, LLMUsage) else LLMUsage()
        record.update(
            {
                "status": "completed" if provider_success else "failed",
                "duration_ms": duration_ms,
                "provider_success": bool(provider_success),
                "runtime_accepted": bool(runtime_accepted),
                "error_code": str(error_code or ""),
                "status_code": status_code if isinstance(status_code, int) else None,
                "retryable": bool(retryable),
                "usage_available": safe_usage.available,
                "input_tokens": safe_usage.input_tokens,
                "output_tokens": safe_usage.output_tokens,
                "total_tokens": safe_usage.total_tokens,
                "reasoning_tokens": safe_usage.reasoning_tokens,
                "cache_read_tokens": safe_usage.cache_read_tokens,
                "cache_write_tokens": safe_usage.cache_write_tokens,
                "finish_reason": str(finish_reason or ""),
            }
        )
        if provider_metadata:
            record["provider_metadata"] = sanitize_unicode(dict(provider_metadata))
        if provider_success:
            self._record_usage(handle.stage, safe_usage, str(finish_reason or ""))
        self.llm_call_ms.append(duration_ms)
        self.prompt_chars.append(int(record["prompt_chars"]))
        self.message_count.append(int(record["message_count"]))
        self.tool_schema_count.append(int(record["tool_schema_count"]))
        self.tool_schema_chars.append(int(record["tool_schema_chars"]))
        if handle.stage in {"tool_call", "agent_continuation", "single_file_read_agent_continuation"}:
            self.tool_call_llm_ms += duration_ms
        return dict(record)

    def _record_usage(
        self,
        stage: str,
        usage: LLMUsage,
        finish_reason: str,
    ) -> None:
        if finish_reason:
            self.finish_reason_counts_by_stage[stage][finish_reason] += 1
        if not usage.available:
            self.llm_usage_unavailable_count += 1
            return
        self.llm_usage_available_count += 1
        values = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
        }
        for name, value in values.items():
            setattr(self, f"llm_{name}", int(getattr(self, f"llm_{name}") or 0) + int(value))
            self.tokens_by_stage[stage][name] += int(value)

    def record_llm_retry_decision(
        self,
        *,
        stage: str,
        attempt_index: int,
        delay_ms: int,
        source: str,
        wait_applied: bool,
        skipped_reason: str = "",
    ) -> None:
        """Attach retry timing to the failed attempt that produced it."""

        normalized_stage = _normalize_stage(stage)
        for record in reversed(self.llm_calls_by_stage.get(normalized_stage, [])):
            if int(record.get("attempt_index") or 0) != int(attempt_index or 0):
                continue
            record.update(
                {
                    "retry_delay_ms": max(0, int(delay_ms or 0)),
                    "retry_delay_source": str(source or ""),
                    "retry_wait_applied": bool(wait_applied),
                    "retry_skipped_reason": str(skipped_reason or ""),
                }
            )
            break

    def record_context_budget(self, decision: Any, *, elapsed_ms: int = 0) -> None:
        """Record one request-level context budget decision."""

        self.context_budget_original_chars.append(int(getattr(decision, "original_chars", 0) or 0))
        self.context_budget_final_chars.append(int(getattr(decision, "final_chars", 0) or 0))
        self.context_budget_saved_chars.append(int(getattr(decision, "saved_chars", 0) or 0))
        self.context_budget_actions.append(str(getattr(decision, "action", "") or ""))
        self.context_budget_records.append(dict(getattr(decision, "to_dict")() if hasattr(decision, "to_dict") else {}))
        self.raw_model_context_tokens = int(getattr(decision, "raw_model_context_tokens", 0) or 0)
        self.raw_model_input_tokens = int(getattr(decision, "raw_model_input_tokens", 0) or 0)
        self.raw_model_output_tokens = int(getattr(decision, "raw_model_output_tokens", 0) or 0)
        self.effective_model_input_tokens = int(getattr(decision, "effective_model_input_tokens", 0) or 0)
        self.requested_output_tokens = int(getattr(decision, "requested_output_tokens", 0) or 0)
        self.reserved_output_tokens = int(getattr(decision, "reserved_output_tokens", 0) or 0)
        self.usable_input_tokens = int(getattr(decision, "usable_input_tokens", 0) or 0)
        self.model_limit_correction_reason = str(getattr(decision, "model_limit_correction_reason", "") or "")
        if str(getattr(decision, "action", "") or "") == "compact":
            self.auto_compact_ms += int(elapsed_ms or 0)

    def record_model_limits(self, config: Any) -> None:
        capabilities = getattr(config, "capabilities", None)
        self.model_limit_source = str(getattr(config, "model_limit_source", "") or "")
        self.model_context_tokens = int(getattr(capabilities, "max_context_tokens", 0) or 0)
        self.model_input_tokens = int(getattr(capabilities, "max_input_tokens", 0) or 0)
        self.model_max_output_tokens = int(getattr(capabilities, "max_output_tokens", 0) or 0)
        self.raw_model_context_tokens = self.model_context_tokens
        self.raw_model_input_tokens = self.model_input_tokens
        self.raw_model_output_tokens = self.model_max_output_tokens
        self.resolved_provider_id = str(getattr(config, "resolved_provider_id", "") or "")
        self.resolved_model_id = str(getattr(config, "resolved_model_id", "") or "")
        self.model_catalog_snapshot_sha256 = str(getattr(config, "model_catalog_snapshot_sha256", "") or "")
        self.model_catalog_upstream_sha256 = str(getattr(config, "model_catalog_upstream_sha256", "") or "")
        self.endpoint_provider_candidates = list(getattr(config, "endpoint_provider_candidates", ()) or ())
        self.model_limit_candidate_models = list(getattr(config, "model_limit_candidate_models", ()) or ())
        self.model_limit_resolution_reason = str(getattr(config, "model_limit_resolution_reason", "unresolved") or "unresolved")
        self.model_limit_ambiguous = bool(getattr(config, "model_limit_ambiguous", False))

    def record_tool_result_reference(self, *, externalized: bool, reused: bool, idempotent_replay: bool) -> None:
        if externalized:
            self.tool_result_externalized += 1
        if reused:
            self.tool_result_ref_reused += 1
        if reused and idempotent_replay:
            self.idempotent_replay_ref_reused += 1

    def record_stage_payload(
        self,
        *,
        stage: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> None:
        """Record prompt-size payload data for a non-LLM stage."""

        normalized_stage = _normalize_stage(stage)
        self.prompt_chars_by_stage[normalized_stage] += _messages_chars(messages)
        self.message_count_by_stage[normalized_stage] += len(messages)
        self.tool_schema_chars_by_stage[normalized_stage] += 0 if not tools else _json_chars(tools)

    def record_prompt_pack(self, pack: Any) -> None:
        """Record stage prompt-pack size details without changing LLM counts."""

        stage = _normalize_stage(str(getattr(pack, "stage", "") or ""))
        record = {
            "stage": stage,
            "pack_name": str(getattr(pack, "pack_name", "") or ""),
            "prompt_chars": int(getattr(pack, "prompt_chars", 0) or 0),
            "message_count": int(getattr(pack, "message_count", 0) or 0),
            "system_chars": int(getattr(pack, "system_chars", 0) or 0),
            "tool_schema_chars": int(getattr(pack, "tool_schema_chars", 0) or 0),
            "metadata": sanitize_unicode(dict(getattr(pack, "metadata", {}) or {})),
        }
        self.prompt_packs_by_stage[stage].append(record)

    def record_pre_router(self, decision: Any) -> None:
        """Record pre-router outcome."""

        metadata = getattr(decision, "metadata", {}) or {}
        self.pre_router_enabled = bool(getattr(decision, "enabled", False))
        self.pre_router_route = str(getattr(decision, "route", "") or "")
        self.pre_router_confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
        self.pre_router_kind = str(metadata.get("pre_router_kind") or "capability")
        self.pre_router_reason = str(getattr(decision, "reason", "") or "")
        self.pre_router_required_capabilities = [
            str(item) for item in (getattr(decision, "required_capabilities", []) or [])
        ]
        self.pre_router_requires_tools = bool(getattr(decision, "requires_tools", False))
        self.pre_router_requires_file_read = bool(getattr(decision, "requires_file_read", False))
        self.pre_router_requires_document_load = bool(
            getattr(decision, "requires_document_load", False)
        )
        self.pre_router_requires_file_write = bool(getattr(decision, "requires_file_write", False))
        self.pre_router_requires_command_exec = bool(getattr(decision, "requires_command_exec", False))
        self.pre_router_requires_network = bool(getattr(decision, "requires_network", False))
        self.pre_router_requires_artifact_output = bool(getattr(decision, "requires_artifact_output", False))
        self.pre_router_requires_code_edit = bool(getattr(decision, "requires_code_edit", False))
        self.pre_router_requires_database = bool(getattr(decision, "requires_database", False))
        self.pre_router_requires_browser = bool(getattr(decision, "requires_browser", False))
        self.pre_router_requires_mcp = bool(getattr(decision, "requires_mcp", False))
        self.pre_router_requires_memory = bool(getattr(decision, "requires_memory", False))
        self.pre_router_requires_git = bool(getattr(decision, "requires_git", False))
        self.pre_router_requires_validation = bool(getattr(decision, "requires_validation", False))
        self.pre_router_requires_multi_step_planning = bool(
            getattr(decision, "requires_multi_step_planning", False)
        )
        self.pre_router_timeout = bool(metadata.get("timeout", False))
        self.pre_router_fallback_reason = str(getattr(decision, "fallback_reason", "") or "")
        self.pre_router_raw_response_chars = int(metadata.get("raw_response_chars", 0) or 0)
        self.pre_router_normalized_route = str(metadata.get("normalized_route") or getattr(decision, "route", "") or "")
        self.pre_router_normalization_reason = str(metadata.get("normalization_reason") or "")

    def record_initial_agent_turn(
        self,
        execution: Any,
        *,
        schema_chars: int,
        direct_tool_execution: bool = False,
    ) -> None:
        """Record the authoritative tools-enabled primary turn."""

        result = getattr(execution, "result", None)
        attempt_records = tuple(getattr(execution, "attempt_records", ()) or ())
        attempt_count = len(attempt_records)
        retry_count = max(0, attempt_count - 1)
        mode = str(getattr(result, "mode", "") or "")
        tool_calls = list(getattr(result, "tool_calls", ()) or ())
        self.initial_agent_turn_ms = int(getattr(execution, "elapsed_ms", 0) or 0)
        self.initial_agent_turn_mode = mode
        self.initial_agent_turn_tool_count = len(tool_calls)
        self.initial_agent_turn_tool_names = [_tool_call_name(call) for call in tool_calls]
        self.initial_agent_turn_schema_chars = int(schema_chars or 0)
        self.initial_agent_turn_direct_tool_execution = bool(direct_tool_execution)
        self.initial_agent_turn_attempt_count = attempt_count
        self.initial_agent_turn_retry_count = retry_count
        self.initial_agent_turn_retry_reason = str(getattr(execution, "retry_reason", "") or "")
        provider_attempt_count = int(
            self.llm_call_count_by_stage["initial_agent_turn"]
        )
        self.initial_agent_turn_attempt_consistent = bool(
            attempt_count
            == int(getattr(execution, "attempt_count", 0) or 0)
            == int(getattr(result, "attempt_count", 0) or 0)
            == provider_attempt_count
        )
        self.initial_agent_turn_retry_delay_ms = int(
            getattr(execution, "retry_delay_ms", 0) or 0
        )
        self.initial_agent_turn_retry_delay_source = str(
            getattr(execution, "retry_delay_source", "") or ""
        )
        self.initial_agent_turn_retry_wait_applied = bool(
            getattr(execution, "retry_wait_applied", False)
        )
        self.initial_agent_turn_retry_skipped_reason = str(
            getattr(execution, "retry_skipped_reason", "") or ""
        )
        self.initial_agent_turn_terminal_failure = mode == "terminal_failure"
        self.initial_agent_turn_failure_category = str(getattr(result, "failure_category", "") or "")
        self.initial_agent_turn_failure_reason = str(getattr(result, "failure_reason", "") or "")
        self.initial_agent_turn_failure_status_code = (
            result.status_code
            if isinstance(getattr(result, "status_code", None), int)
            else None
        )
        self.initial_tool_call_count = len(tool_calls)
    def record_agent_turn_context(self, summary: dict[str, Any]) -> None:
        """Record the latest shared Session context passed to an agent turn."""

        self.agent_turn_history_message_count = int(summary.get("agent_turn_history_message_count") or 0)
        self.agent_turn_history_chars = int(summary.get("agent_turn_history_chars") or 0)
        self.agent_turn_compacted = bool(summary.get("agent_turn_compacted"))
        self.agent_turn_current_user_count = int(summary.get("agent_turn_current_user_count") or 0)

    def record_final_observation_context(self, summary: dict[str, Any]) -> None:
        self.final_observation_count = int(summary.get("observation_count") or 0)
        self.terminal_observation_count = int(summary.get("terminal_observation_count") or self.final_observation_count)
        self.final_observation_call_ids = [str(value) for value in summary.get("call_ids") or [] if str(value or "")]
        self.expected_call_ids = [
            str(value) for value in summary.get("expected_call_ids") or [] if str(value or "")
        ]
        self.represented_call_ids = [
            str(value) for value in summary.get("represented_call_ids") or [] if str(value or "")
        ]
        self.missing_call_ids = [
            str(value) for value in summary.get("missing_call_ids") or [] if str(value or "")
        ]
        self.coverage_complete = bool(summary.get("coverage_complete", True))

    def record_finalization_context_snapshot(self, summary: dict[str, Any]) -> None:
        """Record one immutable finalization snapshot without storing its content."""

        record = sanitize_unicode(dict(summary or {}))
        self.finalization_snapshot_count += 1
        self.finalization_model_context_chars += int(
            record.get("model_context_chars") or 0
        )
        self.finalization_trace_context_chars += int(
            record.get("trace_context_chars") or 0
        )
        if not bool(record.get("coverage_complete", True)):
            self.finalization_context_incomplete_count += 1

    def record_isolated_finalization_pack(self, pack_metadata: dict[str, Any]) -> None:
        """Record one two-message tools-disabled finalization pack."""

        metadata = pack_metadata if isinstance(pack_metadata, dict) else {}
        if bool(metadata.get("finalization_isolated")):
            self.isolated_finalization_pack_count += 1

    def record_finalization_snapshot_reuse(self) -> None:
        """Record reuse of one snapshot by another finalization consumer."""

        self.finalization_snapshot_reuse_count += 1

    def record_finalization_context_budget(self, decision: Any) -> None:
        """Record a hard finalization-context budget failure."""

        if not bool(getattr(decision, "budget_satisfied", True)):
            self.finalization_context_budget_failure_count += 1

    def record_isolated_terminal_synthesis_output(
        self,
        *,
        accepted: bool,
        provider_error: bool = False,
        fallback: bool = False,
    ) -> None:
        """Record only terminal-synthesis boundary outcomes."""

        if provider_error:
            self.isolated_terminal_synthesis_provider_error_count += 1
        elif not accepted:
            self.isolated_terminal_synthesis_output_reject_count += 1
        if fallback:
            self.isolated_terminal_synthesis_fallback_count += 1

    def record_tool_execution(
        self,
        *,
        tool_name: str,
        elapsed_ms: int,
        success: bool,
        error_code: str = "",
        real_execution: bool = True,
        idempotent_replay: bool = False,
        stopped_before_execution: bool = False,
    ) -> None:
        """Record one model ToolCall request and any real dispatch."""

        elapsed = int(elapsed_ms or 0)
        name = str(tool_name or "unknown")
        self.tool_call_count += 1
        if real_execution:
            self.real_tool_execution_count += 1
            self.tool_execution_ms += elapsed
            self.tool_execution_by_name_ms[name] = int(
                self.tool_execution_by_name_ms.get(name, 0)
            ) + elapsed
            self.tool_execution_by_name_count[name] = int(
                self.tool_execution_by_name_count.get(name, 0)
            ) + 1
        if idempotent_replay:
            self.same_call_idempotent_replay_count += 1
        if stopped_before_execution:
            self.exact_tool_loop_stop_count += 1
        self.tool_execution_records.append(
            {
                "tool_name": name,
                "duration_ms": elapsed if real_execution else 0,
                "tool_success": bool(success),
                "tool_error_code": str(error_code or ""),
                "real_execution": bool(real_execution),
                "idempotent_replay": bool(idempotent_replay),
                "stopped_before_execution": bool(stopped_before_execution),
            }
        )

    def record_tool_guard_fast_path(self, decision: Any, *, elapsed_ms: int) -> None:
        """Record one deterministic pre-dispatch execution boundary decision."""

        elapsed = int(elapsed_ms or 0)
        allowed = bool(getattr(decision, "allowed", False))
        status = str(getattr(decision, "status", "") or ("allowed" if allowed else "blocked"))
        self.tool_guard_fast_path_ms += elapsed
        self.tool_guard_fast_path_count += 1
        if allowed:
            self.tool_guard_fast_path_allowed_count += 1
        else:
            self.tool_guard_fast_path_blocked_count += 1
        self.tool_guard_fast_path_records.append(
            {
                "duration_ms": elapsed,
                "allowed": allowed,
                "status": status,
                "policy_code": str(getattr(decision, "code", "") or ""),
                "boundary": str(getattr(decision, "boundary", "") or ""),
                "tool_name": str(getattr(decision, "tool_name", "") or ""),
                "access_mode": str(getattr(decision, "access_mode", "") or ""),
                "sanitized_arguments_present": getattr(decision, "sanitized_arguments", None) is not None,
            }
        )

    def record_capability_surface(self, summary: dict[str, Any]) -> None:
        """Record the effective capability/tool surface used for the tool-call prompt."""

        record = sanitize_unicode(dict(summary or {}))
        self.capability_surface = str(record.get("capability_surface") or "")
        self.effective_capabilities = [str(item) for item in (record.get("effective_capabilities") or [])]
        self.effective_tools = [str(item) for item in (record.get("effective_tools") or [])]
        self.capability_surface_records.append(record)

    def record_permission_tool_universe(
        self,
        *,
        tool_count: int,
        schema_chars: int,
    ) -> None:
        self.permission_tool_universe_count = int(tool_count or 0)
        self.permission_tool_universe_schema_chars = int(schema_chars or 0)

    def record_prompt_pack_compaction(self, pack_metadata: dict[str, Any]) -> None:
        """Record compact continuation-only model projection sizes."""

        metadata = pack_metadata if isinstance(pack_metadata, dict) else {}
        self.continuation_context_projection_chars += int(
            metadata.get("context_projection_chars") or 0
        )
        self.continuation_prompt_overhead_chars += int(
            metadata.get("prompt_overhead_chars") or 0
        )

    def record_observation_compaction(self, summary: dict[str, Any], *, step: int = 0) -> None:
        """Record model-visible observation compaction."""

        record = sanitize_unicode(dict(summary or {}))
        tool = str(record.get("tool_name") or "unknown")
        original = int(record.get("original_chars") or 0)
        compacted = int(record.get("compacted_chars") or 0)
        saved = int(record.get("saved_chars") or max(0, original - compacted))
        self.observation_compaction_count += 1
        self.observation_original_chars_total += original
        self.observation_compacted_chars_total += compacted
        self.observation_saved_chars_total += saved
        current = self.observation_compaction_by_tool.setdefault(tool, {"count": 0, "original_chars": 0, "compacted_chars": 0, "saved_chars": 0})
        current["count"] += 1
        current["original_chars"] += original
        current["compacted_chars"] += compacted
        current["saved_chars"] += saved
        self.max_model_observation_chars = max(self.max_model_observation_chars, compacted)
        self.model_observation_chars_by_step.append({"step": int(step or 0), "tool_name": tool, "chars": compacted})

    def record_build_step_contract(self, summary: dict[str, Any]) -> None:
        """Record build lane step contract creation."""

        record = sanitize_unicode(dict(summary or {}))
        self.build_step_contract_enabled = bool(record.get("enabled", True))
        self.build_step_contract_steps = len(record.get("steps") or [])
        self.build_step_contract_completed_steps = int(record.get("completed_count", 0) or 0)
        self.build_step_contract_current_step = int(record.get("current_step_index", 0) or 0)
        self.build_step_contract_records.append(record)

    def record_build_step_progress(self, summary: dict[str, Any]) -> None:
        """Record build lane step progress after an observation."""

        record = sanitize_unicode(dict(summary or {}))
        self.build_step_contract_enabled = True
        self.build_step_contract_completed_steps = int(record.get("completed_count", 0) or 0)
        self.build_step_contract_current_step = int(record.get("current_step_index", 0) or 0)
        self.build_step_contract_records.append(record)

    def record_tool_scope_budget(self, decision: Any, *, elapsed_ms: int) -> None:
        """Record tool schema budget timing and before/after counts."""

        elapsed = int(elapsed_ms or 0)
        self.tool_scope_budget_ms += elapsed
        self.tool_scope_budget_records.append(
            {
                "duration_ms": elapsed,
                "runtime_lane": str(getattr(decision, "runtime_lane", "") or ""),
                "before_count": int(getattr(decision, "before_count", 0) or 0),
                "after_count": int(getattr(decision, "after_count", 0) or 0),
                "kept_tool_names": list(getattr(decision, "kept_tool_names", []) or []),
                "removed_tool_names": list(getattr(decision, "removed_tool_names", []) or []),
                "reason": str(getattr(decision, "reason", "") or ""),
                "budget_profile": str(getattr(decision, "metadata", {}).get("budget_profile", "") or "")
                if isinstance(getattr(decision, "metadata", None), dict)
                else "",
            }
        )

    def summary(self) -> dict[str, Any]:
        """Return trace-friendly summary data."""

        self.finish()
        return sanitize_unicode(
            {
                "total_ms": self.total_ms,
                "pre_router_enabled": self.pre_router_enabled,
                "pre_router_route": self.pre_router_route,
                "pre_router_confidence": self.pre_router_confidence,
                "pre_router_kind": self.pre_router_kind,
                "pre_router_reason": self.pre_router_reason,
                "pre_router_required_capabilities": list(self.pre_router_required_capabilities),
                "pre_router_requires_tools": self.pre_router_requires_tools,
                "pre_router_requires_file_read": self.pre_router_requires_file_read,
                "pre_router_requires_document_load": self.pre_router_requires_document_load,
                "pre_router_requires_file_write": self.pre_router_requires_file_write,
                "pre_router_requires_command_exec": self.pre_router_requires_command_exec,
                "pre_router_requires_network": self.pre_router_requires_network,
                "pre_router_requires_artifact_output": self.pre_router_requires_artifact_output,
                "pre_router_requires_code_edit": self.pre_router_requires_code_edit,
                "pre_router_requires_database": self.pre_router_requires_database,
                "pre_router_requires_browser": self.pre_router_requires_browser,
                "pre_router_requires_mcp": self.pre_router_requires_mcp,
                "pre_router_requires_memory": self.pre_router_requires_memory,
                "pre_router_requires_git": self.pre_router_requires_git,
                "pre_router_requires_validation": self.pre_router_requires_validation,
                "pre_router_requires_multi_step_planning": self.pre_router_requires_multi_step_planning,
                "pre_router_timeout": self.pre_router_timeout,
                "pre_router_fallback_reason": self.pre_router_fallback_reason,
                "pre_router_raw_response_chars": self.pre_router_raw_response_chars,
                "pre_router_normalized_route": self.pre_router_normalized_route,
                "pre_router_normalization_reason": self.pre_router_normalization_reason,
                "initial_agent_turn_ms": self.initial_agent_turn_ms,
                "initial_agent_turn_mode": self.initial_agent_turn_mode,
                "initial_agent_turn_tool_count": self.initial_agent_turn_tool_count,
                "initial_agent_turn_tool_names": list(self.initial_agent_turn_tool_names),
                "initial_agent_turn_schema_chars": self.initial_agent_turn_schema_chars,
                "initial_agent_turn_direct_tool_execution": self.initial_agent_turn_direct_tool_execution,
                "initial_agent_turn_attempt_count": self.initial_agent_turn_attempt_count,
                "initial_agent_turn_retry_count": self.initial_agent_turn_retry_count,
                "initial_agent_turn_retry_reason": self.initial_agent_turn_retry_reason,
                "initial_agent_turn_terminal_failure": self.initial_agent_turn_terminal_failure,
                "initial_agent_turn_failure_category": self.initial_agent_turn_failure_category,
                "initial_agent_turn_failure_reason": self.initial_agent_turn_failure_reason,
                "initial_agent_turn_failure_status_code": self.initial_agent_turn_failure_status_code,
                "initial_agent_turn_attempt_consistent": self.initial_agent_turn_attempt_consistent,
                "initial_agent_turn_retry_delay_ms": self.initial_agent_turn_retry_delay_ms,
                "initial_agent_turn_retry_delay_source": self.initial_agent_turn_retry_delay_source,
                "initial_agent_turn_retry_wait_applied": self.initial_agent_turn_retry_wait_applied,
                "initial_agent_turn_retry_skipped_reason": self.initial_agent_turn_retry_skipped_reason,
                "agent_turn_history_message_count": self.agent_turn_history_message_count,
                "agent_turn_history_chars": self.agent_turn_history_chars,
                "agent_turn_compacted": self.agent_turn_compacted,
                "agent_turn_current_user_count": self.agent_turn_current_user_count,
                "initial_tool_call_count": self.initial_tool_call_count,
                "terminal_observation_count": self.terminal_observation_count,
                "final_observation_count": self.final_observation_count,
                "final_observation_call_ids": list(self.final_observation_call_ids),
                "expected_call_ids": list(self.expected_call_ids),
                "represented_call_ids": list(self.represented_call_ids),
                "missing_call_ids": list(self.missing_call_ids),
                "coverage_complete": self.coverage_complete,
                "observation_compaction_count": self.observation_compaction_count,
                "observation_original_chars_total": self.observation_original_chars_total,
                "observation_compacted_chars_total": self.observation_compacted_chars_total,
                "observation_saved_chars_total": self.observation_saved_chars_total,
                "observation_compaction_by_tool": dict(self.observation_compaction_by_tool),
                "max_model_observation_chars": self.max_model_observation_chars,
                "model_observation_chars_by_step": list(self.model_observation_chars_by_step),
                "build_step_contract_enabled": self.build_step_contract_enabled,
                "build_step_contract_steps": self.build_step_contract_steps,
                "build_step_contract_completed_steps": self.build_step_contract_completed_steps,
                "build_step_contract_current_step": self.build_step_contract_current_step,
                "build_step_contract_records": list(self.build_step_contract_records),
                "agent_continuation_count": self.agent_continuation_count,
                "single_file_read_agent_continuation_count": self.single_file_read_agent_continuation_count,
                "exceptional_repair_count": self.exceptional_repair_count,
                "direct_agent_prose_adopted_count": self.direct_agent_prose_adopted_count,
                "finalization_snapshot_count": self.finalization_snapshot_count,
                "isolated_finalization_pack_count": self.isolated_finalization_pack_count,
                "finalization_snapshot_reuse_count": self.finalization_snapshot_reuse_count,
                "finalization_context_incomplete_count": self.finalization_context_incomplete_count,
                "finalization_context_budget_failure_count": self.finalization_context_budget_failure_count,
                "finalization_model_context_chars": self.finalization_model_context_chars,
                "finalization_trace_context_chars": self.finalization_trace_context_chars,
                "isolated_terminal_synthesis_output_reject_count": self.isolated_terminal_synthesis_output_reject_count,
                "isolated_terminal_synthesis_provider_error_count": self.isolated_terminal_synthesis_provider_error_count,
                "isolated_terminal_synthesis_fallback_count": self.isolated_terminal_synthesis_fallback_count,
                "continuation_context_projection_chars": self.continuation_context_projection_chars,
                "continuation_prompt_overhead_chars": self.continuation_prompt_overhead_chars,
                "permission_tool_universe_count": self.permission_tool_universe_count,
                "permission_tool_universe_schema_chars": self.permission_tool_universe_schema_chars,
                "memory_prepare_ms": self.memory_prepare_ms,
                "tool_scope_ms": self.tool_scope_ms,
                "tool_scope_budget_ms": self.tool_scope_budget_ms,
                "context_budget_ms": self.context_budget_ms,
                "auto_compact_ms": self.auto_compact_ms,
                "tool_call_llm_ms": self.tool_call_llm_ms,
                "llm_call_count": len(self.llm_call_ms),
                "llm_input_tokens": self.llm_input_tokens,
                "llm_output_tokens": self.llm_output_tokens,
                "llm_total_tokens": self.llm_total_tokens,
                "llm_reasoning_tokens": self.llm_reasoning_tokens,
                "llm_cache_read_tokens": self.llm_cache_read_tokens,
                "llm_cache_write_tokens": self.llm_cache_write_tokens,
                "llm_usage_available_count": self.llm_usage_available_count,
                "llm_usage_unavailable_count": self.llm_usage_unavailable_count,
                "tokens_by_stage": {
                    key: dict(value) for key, value in self.tokens_by_stage.items()
                },
                "finish_reason_counts_by_stage": {
                    key: dict(value)
                    for key, value in self.finish_reason_counts_by_stage.items()
                },
                "llm_call_count_by_stage": dict(self.llm_call_count_by_stage),
                "llm_calls_by_stage": {key: list(value) for key, value in self.llm_calls_by_stage.items()},
                "prompt_chars_by_stage": dict(self.prompt_chars_by_stage),
                "tool_schema_chars_by_stage": dict(self.tool_schema_chars_by_stage),
                "message_count_by_stage": dict(self.message_count_by_stage),
                "prompt_packs_by_stage": {key: list(value) for key, value in self.prompt_packs_by_stage.items()},
                "llm_call_ms": list(self.llm_call_ms),
                "prompt_chars": list(self.prompt_chars),
                "message_count": list(self.message_count),
                "tool_schema_count": list(self.tool_schema_count),
                "tool_schema_chars": list(self.tool_schema_chars),
                "context_budget_original_chars": list(self.context_budget_original_chars),
                "context_budget_final_chars": list(self.context_budget_final_chars),
                "context_budget_saved_chars": list(self.context_budget_saved_chars),
                "context_budget_actions": list(self.context_budget_actions),
                "context_budget_records": list(self.context_budget_records),
                "model_limit_source": self.model_limit_source,
                "model_context_tokens": self.model_context_tokens,
                "model_input_tokens": self.model_input_tokens,
                "model_max_output_tokens": self.model_max_output_tokens,
                "model_output_tokens": self.model_max_output_tokens,
                "raw_model_context_tokens": self.raw_model_context_tokens,
                "raw_model_input_tokens": self.raw_model_input_tokens,
                "raw_model_output_tokens": self.raw_model_output_tokens,
                "effective_model_input_tokens": self.effective_model_input_tokens,
                "requested_output_tokens": self.requested_output_tokens,
                "reserved_output_tokens": self.reserved_output_tokens,
                "usable_input_tokens": self.usable_input_tokens,
                "model_limit_correction_reason": self.model_limit_correction_reason,
                "resolved_provider_id": self.resolved_provider_id,
                "resolved_model_id": self.resolved_model_id,
                "model_catalog_snapshot_sha256": self.model_catalog_snapshot_sha256,
                "model_catalog_upstream_sha256": self.model_catalog_upstream_sha256,
                "endpoint_provider_candidates": list(self.endpoint_provider_candidates),
                "model_limit_candidate_models": list(self.model_limit_candidate_models),
                "model_limit_resolution_reason": self.model_limit_resolution_reason,
                "model_limit_ambiguous": self.model_limit_ambiguous,
                "tool_result_externalized": self.tool_result_externalized,
                "tool_result_ref_reused": self.tool_result_ref_reused,
                "idempotent_replay_ref_reused": self.idempotent_replay_ref_reused,
                "tool_scope_budget_records": list(self.tool_scope_budget_records),
                "tool_call_count": self.tool_call_count,
                "real_tool_execution_count": self.real_tool_execution_count,
                "same_call_idempotent_replay_count": self.same_call_idempotent_replay_count,
                "exact_tool_loop_stop_count": self.exact_tool_loop_stop_count,
                "tool_execution_ms": self.tool_execution_ms,
                "tool_execution_records": list(self.tool_execution_records),
                "tool_guard_fast_path_ms": self.tool_guard_fast_path_ms,
                "tool_guard_fast_path_count": self.tool_guard_fast_path_count,
                "tool_guard_fast_path_allowed_count": self.tool_guard_fast_path_allowed_count,
                "tool_guard_fast_path_blocked_count": self.tool_guard_fast_path_blocked_count,
                "tool_guard_fast_path_records": list(self.tool_guard_fast_path_records),
                "capability_surface": self.capability_surface,
                "effective_capabilities": list(self.effective_capabilities),
                "effective_tools": list(self.effective_tools),
                "capability_surface_records": list(self.capability_surface_records),
                "final_answer_ms": self.final_answer_ms,
                "tool_execution_by_name_ms": dict(self.tool_execution_by_name_ms),
                "tool_execution_by_name_count": dict(self.tool_execution_by_name_count),
            }
        )

def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def elapsed_ms(started: float) -> int:
    """Return elapsed milliseconds for external lightweight timing call sites."""

    return _elapsed_ms(started)


def _normalize_stage(stage: str) -> str:
    value = str(stage or "").strip()
    if value in {
        "initial_agent_turn",
        "tool_call",
        "agent_continuation",
        "final_answer",
        "context_budget",
        "auto_compact",
        "tool_scope_budget",
        "guard",
        "tool_execution",
        "finalization",
        "single_file_read_final_answer",
        "single_file_read_agent_continuation",
    }:
        return value
    return "unknown"


def _llm_options_summary(options: Any | None) -> dict[str, Any]:
    if options is None:
        return {}
    return {
        "llm_profile": str(getattr(options, "stage", "") or ""),
        "max_tokens": getattr(options, "max_tokens", None),
        "timeout": getattr(options, "timeout", None),
        "disable_reasoning": getattr(options, "disable_reasoning", None),
        "temperature": getattr(options, "temperature", None),
    }


def _messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += _json_chars(content)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += _json_chars(tool_calls)
    return total


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception:
        return len(str(value))


def _tool_call_name(call: Any) -> str:
    function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
    value = function.get("name") if isinstance(function, dict) else getattr(function, "name", "")
    return str(value or "")
