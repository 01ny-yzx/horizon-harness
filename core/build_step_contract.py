"""Deterministic build-lane step contracts for mixed capability tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.capability_surface import is_mixed_capability
from core.unicode_safety import sanitize_unicode


STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"

CONTRACT_ROLE_EXECUTION_BATCH = "execution_batch"
CONTRACT_ROLE_TASK_CONTRACT = "task_contract"
INITIAL_TOOL_CALLS_REASON = "initial_tool_calls"


def build_step_contract_role(contract: dict[str, Any] | None) -> str:
    """Return the authoritative semantic role for a build-step contract."""

    reason = str((contract or {}).get("reason") or "")
    if reason == INITIAL_TOOL_CALLS_REASON:
        return CONTRACT_ROLE_EXECUTION_BATCH
    return CONTRACT_ROLE_TASK_CONTRACT


def is_initial_tool_batch_contract(contract: dict[str, Any] | None) -> bool:
    return build_step_contract_role(contract) == CONTRACT_ROLE_EXECUTION_BATCH


def build_step_contract_can_finalize(
    contract: dict[str, Any] | None,
    *,
    completion: dict[str, Any] | None = None,
) -> bool:
    """Decide whether this contract is authoritative for task finalization."""

    if not isinstance(contract, dict) or not contract.get("steps"):
        return False
    if is_initial_tool_batch_contract(contract):
        return False
    if isinstance(completion, dict):
        return bool(completion.get("can_finalize"))
    return bool(contract.get("all_steps_completed") or contract.get("all_steps_resolved"))


@dataclass
class BuildStep:
    index: int
    capability: str
    tool_name: str
    call_id: str = ""
    preferred_tool: str = ""
    candidate_tool_names: list[str] = field(default_factory=list)
    chosen_tool_name: str = ""
    instruction: str = ""
    status: str = STATUS_PENDING
    planned_arguments: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    observation_summary: dict[str, Any] | None = None
    missing_arguments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


@dataclass
class BuildStepContract:
    runtime_lane: str
    reason: str
    contract_role: str
    steps: list[BuildStep]
    current_step_index: int
    completed_count: int
    pending_count: int
    failed_count: int
    blocked_count: int
    all_steps_completed: bool
    all_steps_resolved: bool

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                "runtime_lane": self.runtime_lane,
                "reason": self.reason,
                "contract_role": self.contract_role,
                "steps": [step.to_dict() for step in self.steps],
                "current_step_index": self.current_step_index,
                "completed_count": self.completed_count,
                "pending_count": self.pending_count,
                "failed_count": self.failed_count,
                "blocked_count": self.blocked_count,
                "all_steps_completed": self.all_steps_completed,
                "all_steps_resolved": self.all_steps_resolved,
            }
        )


def build_step_contract_from_task_state(
    task_state: Any,
    *,
    capability_surface: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic step contract from structured metadata."""

    metadata = _metadata(task_state)
    surface = capability_surface if isinstance(capability_surface, dict) else metadata.get("capability_surface")
    surface = surface if isinstance(surface, dict) else {}
    runtime_lane = str(surface.get("runtime_lane") or metadata.get("effective_runtime_lane") or metadata.get("runtime_lane") or "")
    if runtime_lane != "build":
        return {}

    capabilities = _capabilities(metadata, surface)
    tools = set(_string_list(surface.get("effective_tools")))
    if not is_mixed_capability(capabilities):
        return {}

    tool_args = metadata.get("tool_arguments") if isinstance(metadata.get("tool_arguments"), dict) else {}
    plan = metadata.get("effective_tool_plan") if isinstance(metadata.get("effective_tool_plan"), dict) else {}
    steps = _steps_for_capabilities(capabilities, tools, tool_args, plan)
    if len(steps) < 2:
        return {}
    return _contract(steps, reason="mixed_capability_step_contract").to_dict()


def build_step_contract_from_initial_tool_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Build ordered steps from structured calls already emitted by the model."""

    steps: list[BuildStep] = []
    for index, call in enumerate(calls or [], start=1):
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        capability = str(call.get("capability") or "").strip()
        call_id = str(call.get("call_id") or "").strip()
        arguments = dict(call.get("arguments") or {}) if isinstance(call.get("arguments"), dict) else {}
        if not tool_name or not capability or not call_id:
            return {}
        steps.append(
            BuildStep(
                index=index,
                capability=capability,
                tool_name=tool_name,
                call_id=call_id,
                preferred_tool=tool_name,
                candidate_tool_names=[tool_name],
                chosen_tool_name=tool_name,
                instruction=str(call.get("instruction") or f"Execute {tool_name}").strip(),
                arguments=sanitize_unicode(arguments),
                missing_arguments=_missing_arguments(tool_name, arguments),
            )
        )
    return _contract(steps, reason="initial_tool_calls").to_dict() if steps else {}


def update_build_step_contract_with_observation(
    contract: dict[str, Any],
    *,
    tool_name: str,
    observation: Any,
    execution_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance a contract using the latest tool observation."""

    if not isinstance(contract, dict) or not contract.get("steps"):
        return {}
    steps = [dict(step) for step in contract.get("steps", []) if isinstance(step, dict)]
    contract_reason = str(contract.get("reason") or "mixed_capability_step_contract")
    current_index = int(contract.get("current_step_index") or _first_pending_index(steps) or 0)
    current = next((step for step in steps if int(step.get("index") or 0) == current_index), None)
    actual_tool = str(tool_name or _observation_value(observation, "tool_name") or "")
    repeat = False
    unexpected = False
    expected_tool = str(current.get("tool_name") or "") if current is not None else ""
    if current is None or str(current.get("tool_name") or "") != actual_tool:
        repeat = any(
            str(step.get("tool_name") or "") == actual_tool and str(step.get("status") or "") == STATUS_COMPLETED
            for step in steps
        )
        unexpected = not repeat and bool(actual_tool)
    else:
        current["observation_summary"] = _observation_summary(observation)
        current.setdefault("evidence", [])
        status = _status_from_observation(observation)
        current["status"] = status
        current["arguments"] = _merged_execution_arguments(
            current.get("arguments"),
            execution_arguments,
            observation,
        )
        current["missing_arguments"] = _missing_arguments(
            expected_tool,
            dict(current.get("arguments") or {}),
        )
        if status == STATUS_COMPLETED:
            current["evidence"] = [f"tool:{actual_tool}"]
        elif status == STATUS_BLOCKED:
            _block_pending_steps_after_failure(steps, current_index)
        elif status == STATUS_FAILED and contract_reason != "initial_tool_calls":
            _block_pending_steps_after_failure(steps, current_index)

    updated = _contract([_step_from_dict(step) for step in steps], reason=contract_reason).to_dict()
    progress = build_step_progress_summary(updated, last_tool=actual_tool)
    if repeat:
        progress["repeat_tool_warning"] = True
        progress["repeat_tool_name"] = actual_tool
    if unexpected:
        progress["unexpected_tool_warning"] = True
        progress["expected_tool"] = expected_tool
        progress["actual_tool"] = actual_tool
    updated["last_progress"] = progress
    return updated


def build_step_progress_summary(contract: dict[str, Any], *, last_tool: str = "") -> dict[str, Any]:
    steps = [step for step in contract.get("steps", []) if isinstance(step, dict)] if isinstance(contract, dict) else []
    completed = [int(step.get("index") or 0) for step in steps if str(step.get("status") or "") == STATUS_COMPLETED]
    pending = [int(step.get("index") or 0) for step in steps if str(step.get("status") or "") == STATUS_PENDING]
    failed = [int(step.get("index") or 0) for step in steps if str(step.get("status") or "") == STATUS_FAILED]
    blocked = [int(step.get("index") or 0) for step in steps if str(step.get("status") or "") == STATUS_BLOCKED]
    next_step = next((step for step in steps if int(step.get("index") or 0) == int(contract.get("current_step_index") or 0)), {})
    return sanitize_unicode(
        {
            "current_step_index": int(contract.get("current_step_index") or 0) if isinstance(contract, dict) else 0,
            "completed_steps": completed,
            "pending_steps": pending,
            "failed_steps": failed,
            "blocked_steps": blocked,
            "last_completed_tool": str(last_tool or ""),
            "next_tool": str(next_step.get("chosen_tool_name") or next_step.get("tool_name") or ""),
            "next_candidate_tools": _string_list(next_step.get("candidate_tool_names")),
            "all_steps_completed": bool(contract.get("all_steps_completed")) if isinstance(contract, dict) else False,
            "all_steps_resolved": bool(contract.get("all_steps_resolved")) if isinstance(contract, dict) else False,
            "completed_count": int(contract.get("completed_count") or 0) if isinstance(contract, dict) else 0,
            "pending_count": int(contract.get("pending_count") or 0) if isinstance(contract, dict) else 0,
            "failed_count": int(contract.get("failed_count") or 0) if isinstance(contract, dict) else 0,
            "blocked_count": int(contract.get("blocked_count") or 0) if isinstance(contract, dict) else 0,
        }
    )


def resolve_build_contract_completion(task_state: Any) -> dict[str, Any]:
    """Resolve whether a structured build contract has no executable work left."""

    metadata = _metadata(task_state)
    contract = metadata.get("build_step_contract")
    if not isinstance(contract, dict) or not contract.get("steps"):
        return {}
    steps = [step for step in contract.get("steps", []) if isinstance(step, dict)]
    completed = _step_ids(steps, STATUS_COMPLETED)
    pending = _step_ids(steps, STATUS_PENDING)
    failed = _step_ids(steps, STATUS_FAILED)
    blocked = _step_ids(steps, STATUS_BLOCKED)
    observations = [item for item in metadata.get("completion_observations") or [] if isinstance(item, dict)]
    contract_role = build_step_contract_role(contract)
    initial_tool_batch = is_initial_tool_batch_contract(contract)
    all_steps_completed = bool(steps and len(completed) == len(steps))
    all_steps_resolved = bool(steps and not pending)
    all_steps_have_terminal_observation = all(
        str(step.get("status") or "") in {STATUS_COMPLETED, STATUS_FAILED, STATUS_BLOCKED}
        and isinstance(step.get("observation_summary"), dict)
        for step in steps
    )
    policy_blocked = any(
        str((step.get("observation_summary") or {}).get("policy_code") or "").strip()
        for step in steps
        if isinstance(step.get("observation_summary"), dict)
    )
    completion_evidence_available = bool(
        all_steps_resolved and all_steps_have_terminal_observation and observations
    )
    can_finalize = build_step_contract_can_finalize(
        contract,
        completion={"can_finalize": completion_evidence_available},
    )
    if initial_tool_batch:
        reason = "initial_tool_batch_complete" if all_steps_resolved else "initial_tool_batch_pending"
    elif all_steps_completed:
        reason = "build_steps_complete"
    elif policy_blocked and can_finalize:
        reason = "build_steps_policy_blocked"
    elif (failed or blocked) and can_finalize:
        reason = "build_steps_resolved_failure"
    else:
        reason = "build_steps_pending"
    return sanitize_unicode(
        {
            "all_steps_completed": all_steps_completed,
            "all_steps_resolved": all_steps_resolved,
            "contract_role": contract_role,
            "initial_tool_batch_complete": bool(initial_tool_batch and all_steps_resolved),
            "has_pending_steps": bool(pending),
            "tool_count": len(steps),
            "completed_count": len(completed),
            "pending_count": len(pending),
            "failed_count": len(failed),
            "blocked_count": len(blocked),
            "completed_step_ids": completed,
            "pending_step_ids": pending,
            "failed_step_ids": failed,
            "blocked_step_ids": blocked,
            "required_capabilities_satisfied": bool(not initial_tool_batch and all_steps_completed),
            "all_steps_have_terminal_observation": all_steps_have_terminal_observation,
            "has_completed_observations": bool(observations),
            "observation_count": len(observations),
            "can_finalize": can_finalize,
            "reason": reason,
        }
    )


def current_build_step(contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, dict):
        return {}
    index = int(contract.get("current_step_index") or 0)
    for step in contract.get("steps", []) or []:
        if isinstance(step, dict) and int(step.get("index") or 0) == index:
            return sanitize_unicode(dict(step))
    return {}


def _steps_for_capabilities(
    capabilities: list[str],
    tools: set[str],
    tool_args: dict[str, Any],
    plan: dict[str, Any],
) -> list[BuildStep]:
    cap_set = set(capabilities)
    order: list[tuple[str, str]] = []
    if {"file_read", "command_exec"} <= cap_set and {"read_file", "sandbox_exec"} <= tools:
        order = [("file_read", "read_file"), ("command_exec", "sandbox_exec")]
    elif {"file_read", "file_write"} <= cap_set and {"read_file", "write_file"} <= tools:
        order = [("file_read", "read_file"), ("file_write", "write_file")]
    elif {"file_write", "command_exec"} <= cap_set and {"write_file", "sandbox_exec"} <= tools:
        order = [("file_write", "write_file"), ("command_exec", "sandbox_exec")]
    else:
        return []

    steps: list[BuildStep] = []
    for index, (capability, tool_name) in enumerate(order, start=1):
        arguments = _arguments_for(capability, tool_name, tool_args, plan)
        missing = _missing_arguments(tool_name, arguments)
        steps.append(
            BuildStep(
                index=index,
                capability=capability,
                tool_name=tool_name,
                arguments=arguments,
                missing_arguments=missing,
            )
        )
    return steps


def _arguments_for(capability: str, tool_name: str, tool_args: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    nested = tool_args.get(capability)
    if isinstance(nested, dict):
        return sanitize_unicode(dict(nested))
    nested = tool_args.get(tool_name)
    if isinstance(nested, dict):
        return sanitize_unicode(dict(nested))
    result: dict[str, Any] = {}
    if tool_name == "read_file":
        path = tool_args.get("path") or plan.get("path")
        if path:
            result["path"] = path
    elif tool_name == "write_file":
        path = tool_args.get("path") or plan.get("path")
        content = tool_args.get("content") or plan.get("content")
        if path:
            result["path"] = path
        if content:
            result["content"] = content
    elif tool_name == "sandbox_exec":
        command = tool_args.get("command") or plan.get("command")
        if command:
            result["command"] = command
    return sanitize_unicode(result)


def _missing_arguments(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name == "read_file":
        required = ("path",)
    elif tool_name == "write_file":
        required = ("path", "content")
    elif tool_name == "sandbox_exec":
        required = ("command",)
    else:
        required = ()
    return [name for name in required if not str(arguments.get(name) or "").strip()]


def _merged_execution_arguments(
    existing: Any,
    execution_arguments: dict[str, Any] | None,
    observation: Any,
) -> dict[str, Any]:
    result = dict(existing or {}) if isinstance(existing, dict) else {}
    observed_arguments = _observation_value(observation, "arguments")
    if isinstance(observed_arguments, dict):
        result.update(observed_arguments)
    if isinstance(execution_arguments, dict):
        result.update(execution_arguments)
    return sanitize_unicode(result)


def _block_pending_steps_after_failure(steps: list[dict[str, Any]], failed_index: int) -> None:
    for step in steps:
        if int(step.get("index") or 0) <= failed_index:
            continue
        if str(step.get("status") or "") != STATUS_PENDING:
            continue
        step["status"] = STATUS_BLOCKED
        step["observation_summary"] = {
            "status": STATUS_BLOCKED,
            "success": False,
            "error_code": "blocked_by_previous_failure",
            "policy_code": "",
            "tool_name": str(step.get("tool_name") or ""),
            "exit_code": None,
            "output_path": "",
        }
        evidence = list(step.get("evidence") or [])
        evidence.append("blocked_by_previous_failure")
        step["evidence"] = evidence


def _contract(steps: list[BuildStep], *, reason: str) -> BuildStepContract:
    completed = sum(1 for step in steps if step.status == STATUS_COMPLETED)
    pending = sum(1 for step in steps if step.status == STATUS_PENDING)
    failed = sum(1 for step in steps if step.status == STATUS_FAILED)
    blocked = sum(1 for step in steps if step.status == STATUS_BLOCKED)
    current = next((step.index for step in steps if step.status == STATUS_PENDING), 0)
    return BuildStepContract(
        runtime_lane="build",
        reason=reason,
        contract_role=build_step_contract_role({"reason": reason}),
        steps=steps,
        current_step_index=current,
        completed_count=completed,
        pending_count=pending,
        failed_count=failed,
        blocked_count=blocked,
        all_steps_completed=bool(steps and completed == len(steps)),
        all_steps_resolved=bool(steps and pending == 0),
    )


def _capabilities(metadata: dict[str, Any], surface: dict[str, Any]) -> list[str]:
    for key in ("effective_required_capabilities", "required_capabilities"):
        values = _string_list(metadata.get(key))
        if values:
            return values
    return _string_list(surface.get("effective_capabilities"))


def _metadata(task_state: Any) -> dict[str, Any]:
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    return metadata if isinstance(metadata, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result
    return []


def _step_from_dict(value: dict[str, Any]) -> BuildStep:
    return BuildStep(
        index=int(value.get("index") or 0),
        capability=str(value.get("capability") or ""),
        tool_name=str(value.get("tool_name") or ""),
        call_id=str(value.get("call_id") or ""),
        preferred_tool=str(value.get("preferred_tool") or ""),
        candidate_tool_names=_string_list(value.get("candidate_tool_names")),
        chosen_tool_name=str(value.get("chosen_tool_name") or ""),
        instruction=str(value.get("instruction") or ""),
        status=str(value.get("status") or STATUS_PENDING),
        planned_arguments=dict(
            value.get("planned_arguments")
            if isinstance(value.get("planned_arguments"), dict)
            else value.get("arguments")
            or {}
        ),
        arguments=dict(value.get("arguments") or {}),
        evidence=list(value.get("evidence") or []),
        observation_summary=dict(value.get("observation_summary") or {}) if value.get("observation_summary") else None,
        missing_arguments=list(value.get("missing_arguments") or []),
    )


def _first_pending_index(steps: list[dict[str, Any]]) -> int:
    for step in steps:
        if str(step.get("status") or "") == STATUS_PENDING:
            return int(step.get("index") or 0)
    return 0


def _step_ids(steps: list[dict[str, Any]], status: str) -> list[int]:
    return [int(step.get("index") or 0) for step in steps if str(step.get("status") or "") == status]


def _status_from_observation(observation: Any) -> str:
    status = str(_observation_value(observation, "status") or "").lower()
    success = bool(_observation_value(observation, "success"))
    if status == "blocked":
        return STATUS_BLOCKED
    if status in {"failed", "error"}:
        return STATUS_FAILED
    if success or status == "success":
        return STATUS_COMPLETED
    return STATUS_FAILED


def _observation_summary(observation: Any) -> dict[str, Any]:
    return sanitize_unicode(
        {
            "tool_name": str(_observation_value(observation, "tool_name") or ""),
            "call_id": str(_observation_value(observation, "call_id") or ""),
            "provider_call_id": str(_observation_value(observation, "provider_call_id") or ""),
            "status": str(_observation_value(observation, "status") or ""),
            "success": bool(_observation_value(observation, "success")),
            "error_code": str(_observation_value(observation, "error_code") or ""),
            "policy_code": str(_observation_value(observation, "policy_code") or ""),
            "exit_code": _observation_value(observation, "exit_code"),
            "output_path": str(_observation_value(observation, "output_path") or ""),
        }
    )


def _observation_value(observation: Any, key: str) -> Any:
    if isinstance(observation, dict):
        return observation.get(key)
    return getattr(observation, key, None)
