"""Opt-in provider streaming scoped to one execution thread.

Reasoning is retained as provider metadata, never emitted as user-visible text.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
import threading
from types import SimpleNamespace
from typing import Any
import uuid

from openai.types.chat import ChatCompletionMessage

from providers.base import REASONING_METADATA_KEYS


@dataclass(frozen=True)
class StreamContext:
    emit: Callable[[str, dict[str, Any]], Any]
    cancelled: threading.Event

    def check(self) -> None:
        if self.cancelled.is_set():
            raise InterruptedError("Session execution interrupted")


_current: ContextVar[StreamContext | None] = ContextVar("provider_live_stream", default=None)
VISIBLE_STAGES = frozenset({"initial_agent_turn", "agent_continuation", "final_answer",
                            "simple_fast_path", "single_file_read_agent_continuation"})


@contextmanager
def live_stream(emit: Callable[[str, dict[str, Any]], Any], cancelled: threading.Event) -> Iterator[None]:
    token = _current.set(StreamContext(emit, cancelled))
    try:
        yield
    finally:
        _current.reset(token)


def stream_context(stage: str) -> StreamContext | None:
    return _current.get() if stage in VISIBLE_STAGES else None


def consume_completion(stream: Any, context: StreamContext, call_id: str) -> Any:
    """Assemble an SDK-compatible completion while forwarding text immediately."""
    text: list[str] = []
    reasoning: dict[str, str] = {}
    calls: dict[int, dict[str, Any]] = {}
    usage = None
    finish = ""
    done = threading.Event()

    def close_on_cancel() -> None:
        while not done.wait(.05):
            if context.cancelled.is_set():
                try:
                    stream.close()
                except Exception:
                    pass
                return

    watcher = threading.Thread(target=close_on_cancel, daemon=True, name="horizon-stream-cancel")
    watcher.start()
    try:
        for chunk in stream:
            context.check()
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            for choice in getattr(chunk, "choices", ()):
                if getattr(choice, "index", 0) != 0:
                    continue
                finish = getattr(choice, "finish_reason", None) or finish
                delta = choice.delta
                content = getattr(delta, "content", None)
                if content:
                    text.append(content)
                    context.emit("llm.delta", {"call_id": call_id, "text": content})
                for key in REASONING_METADATA_KEYS:
                    value = getattr(delta, key, None)
                    if isinstance(value, str):
                        reasoning[key] = reasoning.get(key, "") + value
                for tool in getattr(delta, "tool_calls", None) or ():
                    call = calls.setdefault(tool.index, {"id": "", "type": "function",
                                                        "function": {"name": "", "arguments": ""}})
                    if tool.id:
                        call["id"] += tool.id
                    function = tool.function
                    if function:
                        call["function"]["name"] += function.name or ""
                        call["function"]["arguments"] += function.arguments or ""
        context.check()
        if not finish:
            raise ValueError("Provider stream ended without a finish reason")
        message = ChatCompletionMessage.model_validate({
            "role": "assistant", "content": "".join(text) or None,
            "tool_calls": [calls[i] for i in sorted(calls)] or None, **reasoning,
        })
        context.emit("llm.ended", {"call_id": call_id, "finish_reason": finish})
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage)
    finally:
        done.set()
        with suppress(Exception):
            stream.close()
        watcher.join(timeout=.2)


def new_call_id() -> str:
    return f"stream_{uuid.uuid4().hex}"
