"""Focused smoke for synchronous CLI Session interaction."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.session_message import SessionMessage
from main import _render_session_turn_output, _run_session_turn


def _message(
    *,
    message_id: str,
    message_type: str,
    seq: int,
    data: dict,
) -> SessionMessage:
    return SessionMessage(
        id=message_id,
        session_id="ses_cli",
        type=message_type,
        seq=seq,
        time_created=seq,
        time_updated=seq,
        data=data,
    )


def test_assistant_text_after_admission() -> None:
    messages = [
        _message(
            message_id="msg_user",
            message_type="user",
            seq=11,
            data={"text": "hello"},
        ),
        _message(
            message_id="msg_assistant",
            message_type="assistant",
            seq=12,
            data={
                "content": [
                    {"type": "text", "text": "Horizon "},
                    {"type": "text", "text": "Session normal"},
                ]
            },
        ),
    ]
    assert _render_session_turn_output(messages, after_seq=10) == (
        "Horizon Session normal"
    )


def test_previous_assistant_is_excluded() -> None:
    messages = [
        _message(
            message_id="msg_old",
            message_type="assistant",
            seq=9,
            data={"content": [{"type": "text", "text": "old answer"}]},
        ),
        _message(
            message_id="msg_new",
            message_type="assistant",
            seq=12,
            data={"content": [{"type": "text", "text": "new answer"}]},
        ),
    ]
    assert _render_session_turn_output(messages, after_seq=10) == "new answer"


def test_tool_calls_are_not_rendered() -> None:
    messages = [
        _message(
            message_id="msg_tool",
            message_type="assistant",
            seq=12,
            data={
                "content": [
                    {
                        "type": "tool",
                        "id": "call_1",
                        "name": "read_file",
                        "state": {"status": "completed"},
                    }
                ]
            },
        ),
        _message(
            message_id="msg_final",
            message_type="assistant",
            seq=15,
            data={"content": [{"type": "text", "text": "final answer"}]},
        ),
    ]
    assert _render_session_turn_output(messages, after_seq=10) == "final answer"


def test_synthetic_output_is_rendered() -> None:
    messages = [
        _message(
            message_id="msg_synthetic",
            message_type="synthetic",
            seq=13,
            data={"text": "runtime final output"},
        )
    ]
    assert _render_session_turn_output(messages, after_seq=10) == (
        "runtime final output"
    )


class _FakeSessions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.messages = [
            _message(
                message_id="msg_final",
                message_type="assistant",
                seq=12,
                data={"content": [{"type": "text", "text": "done"}]},
            )
        ]

    def prompt(self, session_id: str, user_input: str, *, resume: bool = True):
        self.calls.append(("prompt", session_id, user_input, resume))
        return SimpleNamespace(admitted_seq=10)

    def resume(self, session_id: str) -> None:
        self.calls.append(("resume", session_id))

    def context(self, session_id: str) -> list[SessionMessage]:
        self.calls.append(("context", session_id))
        return self.messages


def test_cli_uses_admit_then_synchronous_resume() -> None:
    sessions = _FakeSessions()
    assert _run_session_turn(sessions, "ses_cli", "hello") == "done"
    assert sessions.calls == [
        ("prompt", "ses_cli", "hello", False),
        ("resume", "ses_cli"),
        ("context", "ses_cli"),
    ]


def main() -> None:
    test_assistant_text_after_admission()
    test_previous_assistant_is_excluded()
    test_tool_calls_are_not_rendered()
    test_synthetic_output_is_rendered()
    test_cli_uses_admit_then_synchronous_resume()
    print("smoke_cli_session_interaction ok")


if __name__ == "__main__":
    main()
