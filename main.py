"""Command-line entry point for the AI Agent."""

from __future__ import annotations

from config.settings import settings
from core.agent_factory import create_session_for_workspace
from core.session import SessionService


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
            admitted = sessions.prompt(session.id, user_input)
        except Exception as exc:  # noqa: BLE001
            print(f"\nPrompt admission failed: {exc}")
            continue
        print("\nPrompt admitted:")
        print(
            f"session_id={admitted.session_id} message_id={admitted.id} "
            f"admitted_seq={admitted.admitted_seq} delivery={admitted.delivery}"
        )


if __name__ == "__main__":
    main()
