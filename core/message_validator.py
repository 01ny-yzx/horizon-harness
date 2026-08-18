"""Validation for OpenAI chat-completions tool-call message ordering."""

from __future__ import annotations

from typing import Any


class ToolMessageProtocolError(ValueError):
    """Raised when chat history violates the OpenAI tool-call protocol."""


def validate_openai_tool_messages(messages: list[dict[str, Any]]) -> None:
    """Ensure every assistant tool call is immediately answered by tool messages.

    OpenAI chat completions require an assistant message with ``tool_calls`` to
    be followed by one ``role="tool"`` message for every requested
    ``tool_call.id`` before any other role appears.
    """

    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")

        if role == "tool":
            raise ToolMessageProtocolError(
                _format_error(index, "orphan tool message without a preceding assistant tool_calls message")
            )

        tool_calls = message.get("tool_calls") if role == "assistant" else None
        if not tool_calls:
            index += 1
            continue

        expected_ids = [_tool_call_id(tool_call) for tool_call in tool_calls]
        missing_id = next((tool_id for tool_id in expected_ids if not tool_id), None)
        if missing_id is not None or any(not tool_id for tool_id in expected_ids):
            raise ToolMessageProtocolError(_format_error(index, "assistant tool_call is missing an id"))

        following = messages[index + 1 : index + 1 + len(expected_ids)]
        if len(following) < len(expected_ids):
            missing = expected_ids[len(following) :]
            raise ToolMessageProtocolError(
                _format_error(index, f"missing tool messages for tool_call_id(s): {', '.join(missing)}")
            )

        seen_ids: list[str] = []
        for offset, tool_message in enumerate(following, start=1):
            if tool_message.get("role") != "tool":
                covered = {tool.get("tool_call_id") for tool in following[: offset - 1]}
                missing = [tool_id for tool_id in expected_ids if tool_id not in covered]
                raise ToolMessageProtocolError(
                    _format_error(
                        index + offset,
                        f"assistant tool_calls must be followed immediately by tool messages; "
                        f"missing tool_call_id(s): {', '.join(missing)}",
                    )
                )
            tool_call_id = str(tool_message.get("tool_call_id") or "")
            if not tool_call_id:
                raise ToolMessageProtocolError(_format_error(index + offset, "tool message is missing tool_call_id"))
            seen_ids.append(tool_call_id)

        unexpected = [tool_id for tool_id in seen_ids if tool_id not in expected_ids]
        if unexpected:
            raise ToolMessageProtocolError(
                _format_error(index, f"unexpected tool_call_id(s): {', '.join(unexpected)}")
            )

        missing = [tool_id for tool_id in expected_ids if tool_id not in seen_ids]
        if missing:
            raise ToolMessageProtocolError(
                _format_error(index, f"missing tool messages for tool_call_id(s): {', '.join(missing)}")
            )

        if len(set(seen_ids)) != len(seen_ids):
            raise ToolMessageProtocolError(_format_error(index, "duplicate tool_call_id in following tool messages"))

        index += 1 + len(expected_ids)


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def _format_error(index: int, detail: str) -> str:
    return f"Invalid OpenAI tool-call message history at messages[{index}]: {detail}."
