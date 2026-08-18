"""Verify sandbox_exec commands are never rerouted to internal tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PREVIOUS_ACCESS_MODE = os.environ.get("AGENT_ACCESS_MODE")
os.environ["AGENT_ACCESS_MODE"] = "full_access"

from core.loop import AgentLoop
from core.memory import Memory
from providers.mock import MockProvider, assistant_message


def _call(call_id: str, command: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="sandbox_exec",
            arguments=json.dumps({"command": command}),
        ),
    )


def _run(command: str, *, success: bool) -> tuple[list[str], dict[str, Any], Any]:
    llm = MockProvider(
        responses=[
            assistant_message("", [_call("shell-call", command)]),
            assistant_message("Shell result reported."),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=3)
    captured: dict[str, Any] = {}
    received: list[str] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    def sandbox_exec(command: str, **_: Any) -> dict[str, Any]:
        received.append(command)
        return {
            "success": success,
            "status": "success" if success else "failed",
            "error": "command failed" if not success else "",
            "error_code": "command_failed" if not success else "",
            "data": {"command": command, "exit_code": 0 if success else 127},
        }

    def status_should_not_run(**_: Any) -> dict[str, Any]:
        raise AssertionError("internal status handler must not be called")

    loop._finish_with_trace = MethodType(finish, loop)
    loop.tools["sandbox_exec"] = sandbox_exec
    loop.tools["get_usage_status"] = status_should_not_run
    assert loop.run("Run the supplied shell command.") == "Shell result reported."
    return received, captured, llm


def main() -> None:
    try:
        for command in (
            "get_usage_status",
            "get_usage_status --json",
            "get_usage_status | cat",
            "get_usage_status && echo ok",
        ):
            received, captured, _ = _run(command, success=True)
            assert received == [command]
            grant = captured["state"].metadata["tool_call_grant_ledger"]["shell-call"]
            assert grant["executable_name"] == "sandbox_exec"
            observations = captured["state"].metadata["completion_observations"]
            assert observations[0]["tool"] == "sandbox_exec"
            old_prefix = "tool_shell" + "_collision"
            assert not any(
                str(event.event_type).startswith(old_prefix)
                for event in captured["trace"].events
            )

        command = "get_usage_status"
        received, captured, _ = _run(command, success=False)
        assert received == [command]
        observation = captured["state"].metadata["completion_observations"][0]
        assert observation["tool"] == "sandbox_exec"
        assert observation["success"] is False
        assert observation["data"]["command"] == command
    finally:
        if _PREVIOUS_ACCESS_MODE is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = _PREVIOUS_ACCESS_MODE

    print("smoke_shell_tool_passthrough ok")


if __name__ == "__main__":
    main()
