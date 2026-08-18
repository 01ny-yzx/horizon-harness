"""Structured workflow definitions and runtime state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.state import PlanStep, StepStatus


@dataclass(frozen=True)
class WorkflowStep:
    """Static workflow step definition."""

    name: str
    instruction: str
    required: bool = True
    evidence_required: bool = False
    max_retries: int = 0
    can_skip: bool = False
    default_skip_reason: str = ""

    def to_plan_step(self, index: int) -> PlanStep:
        return PlanStep(index=index, name=self.name, instruction=self.instruction)


@dataclass
class WorkflowStepState:
    """Runtime state for one workflow step."""

    index: int
    step: WorkflowStep
    status: StepStatus = "pending"
    attempts: int = 0
    evidence: list[str] = field(default_factory=list)
    error: str = ""
    skip_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def mark_running(self) -> None:
        self.status = "running"

    def mark_completed(self, evidence: str = "") -> None:
        self.status = "completed"
        self.error = ""
        if evidence:
            self.evidence.append(evidence)

    def mark_failed(self, error: str = "") -> None:
        self.status = "failed"
        self.error = error
        self.attempts += 1

    def mark_skipped(self, reason: str = "") -> None:
        self.status = "skipped"
        self.skip_reason = reason or self.step.default_skip_reason or "Skipped."

    def can_retry(self) -> bool:
        return self.attempts <= self.step.max_retries

    def to_plan_step(self) -> PlanStep:
        evidence = "; ".join(self.evidence)
        error = self.error or self.skip_reason
        return PlanStep(
            index=self.index,
            name=self.step.name,
            instruction=self.step.instruction,
            status=self.status,
            evidence=evidence,
            error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step": asdict(self.step),
            "status": self.status,
            "attempts": self.attempts,
            "evidence": self.evidence,
            "error": self.error,
            "skip_reason": self.skip_reason,
            "metadata": self.metadata,
        }


@dataclass
class WorkflowState:
    """Runtime state for one selected workflow."""

    workflow_name: str
    workflow_kind: str
    task_type: str
    steps: list[WorkflowStepState]
    current_step_index: int = 0
    evidence: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def current_step(self) -> WorkflowStepState | None:
        if not self.steps:
            return None
        if self.current_step_index < 0 or self.current_step_index >= len(self.steps):
            return None
        return self.steps[self.current_step_index]

    def next_pending_step(self) -> WorkflowStepState | None:
        return next((step for step in self.steps if step.status == "pending"), None)

    def mark_step_running(self, step_name: str) -> None:
        step = self._find_step(step_name)
        if step:
            step.mark_running()
            self.current_step_index = step.index

    def mark_step_completed(self, step_name: str, evidence: str = "") -> None:
        step = self._find_step(step_name)
        if not step:
            return
        step.mark_completed(evidence)
        if evidence:
            self.evidence.append(evidence)
        next_step = self.next_pending_step()
        if next_step:
            self.current_step_index = next_step.index

    def mark_step_failed(self, step_name: str, error: str = "") -> None:
        step = self._find_step(step_name)
        if not step:
            return
        step.mark_failed(error)
        self.current_step_index = step.index
        if error:
            self.issues.append(error)

    def mark_step_skipped(self, step_name: str, reason: str = "") -> None:
        step = self._find_step(step_name)
        if not step:
            return
        step.mark_skipped(reason)
        next_step = self.next_pending_step()
        if next_step:
            self.current_step_index = next_step.index

    def has_failed_required_step(self) -> bool:
        return any(step.step.required and step.status == "failed" for step in self.steps)

    def is_complete(self) -> bool:
        if self.has_failed_required_step():
            return False
        return all(step.status in {"completed", "skipped"} for step in self.steps)

    def to_plan_steps(self) -> list[PlanStep]:
        return [step.to_plan_step() for step in self.steps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "workflow_kind": self.workflow_kind,
            "task_type": self.task_type,
            "steps": [step.to_dict() for step in self.steps],
            "current_step_index": self.current_step_index,
            "evidence": self.evidence,
            "issues": self.issues,
            "metadata": self.metadata,
        }

    def format_for_prompt(self) -> str:
        lines = [
            f"WorkflowState: name={self.workflow_name} kind={self.workflow_kind} task_type={self.task_type}",
            f"- current_step_index: {self.current_step_index}",
            "- steps:",
        ]
        for step in self.steps:
            detail = "; ".join(step.evidence) or step.error or step.skip_reason or step.step.instruction
            lines.append(f"  {step.index}. {step.step.name} [{step.status}] - {detail}")
        return "\n".join(lines)

    def _find_step(self, step_name: str) -> WorkflowStepState | None:
        return next((step for step in self.steps if step.step.name == step_name), None)


@dataclass
class WorkflowRunResult:
    """Initial or final workflow runner result."""

    success: bool
    workflow_state: WorkflowState
    final_status: str
    issues: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "workflow_state": self.workflow_state.to_dict(),
            "final_status": self.final_status,
            "issues": self.issues,
            "metadata": self.metadata,
        }
