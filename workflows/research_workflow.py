"""Structured workflow for research tasks."""

from __future__ import annotations

from core.workflow_types import WorkflowStep


class ResearchWorkflow:
    """Describe the expected web research flow for the Agent."""

    def __init__(self) -> None:
        self.steps = self.build_steps()

    def build_steps(self) -> list[WorkflowStep]:
        return [
            WorkflowStep("research", "Use the current tool surface as needed to obtain information for the user's request."),
            WorkflowStep("respond", "Produce the user-facing response from the conversation and available tool observations."),
        ]
