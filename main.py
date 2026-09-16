"""Command-line entry point for the AI Agent."""

from __future__ import annotations

from collections.abc import Iterable

from config.settings import settings
from core.agent_factory import create_session_for_workspace
from core.session import SessionService
from core.session_message import SessionMessage


def _render_session_turn_output(
    messages: Iterable[SessionMessage],
    *,
    after_seq: int,
) -> str | None:
    """Return the last user-visible output created after one admission."""

    output: str | None = None
    for message in messages:
        if message.seq <= after_seq:
            continue
        if message.type == "synthetic":
            text = str(message.data.get("text") or "")
        elif message.type == "assistant":
            content = message.data.get("content")
            if not isinstance(content, list):
                continue
            text = "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            continue
        if text:
            output = text
    return output


def _run_session_turn(
    sessions: SessionService,
    session_id: str,
    user_input: str,
) -> str | None:
    """Admit one CLI prompt, synchronously run it, and read this turn's output."""

    admitted = sessions.prompt(
        session_id,
        user_input,
        resume=False,
    )
    sessions.resume(session_id)
    return _render_session_turn_output(
        sessions.context(session_id),
        after_seq=admitted.admitted_seq,
    )


def main() -> None:
    """Start an interactive terminal session."""

    print("Command-line AI Agent started. Type exit or quit to leave.")
    print(f"Current LLM: {settings.llm_provider} / {settings.llm_model}")
    print("Example: help me inspect files in the current directory")

    workspace, session = create_session_for_workspace()
    sessions = SessionService(database_path=workspace.database_path)
    print(f"Session ID: {session.id}")

    while True:
        try:
            user_input = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExited.")
            break

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            print("Exited.")
            break

        try:
            output = _run_session_turn(sessions, session.id, user_input)
        except Exception as exc:  # noqa: BLE001
            print(f"\nSession execution failed: {exc}")
            continue
        if output is not None:
            print(f"\n{output}")


if __name__ == "__main__":
    main()
