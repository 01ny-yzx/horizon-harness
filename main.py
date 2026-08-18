"""Command-line entry point for the AI Agent."""

from __future__ import annotations

import traceback

from config.settings import settings
from core.agent_factory import build_agent_for_workspace
from providers.openai_compatible import LLMProviderError


def main() -> None:
    """Start an interactive terminal session."""

    print("Command-line AI Agent started. Type exit or quit to leave.")
    print(f"Current LLM: {settings.llm_provider} / {settings.llm_model}")
    print("Example: help me inspect files in the current directory")

    agent = build_agent_for_workspace()

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
            final_answer = agent.run(user_input)
        except LLMProviderError as exc:
            if settings.debug_mode:
                traceback.print_exc()
            final_answer = (
                "模型服务调用失败，请检查模型服务、API Key、网络或模型配置。"
                f"\n简要原因：{exc}"
            )
        print("\nFinal Answer:")
        print(final_answer)


if __name__ == "__main__":
    main()
