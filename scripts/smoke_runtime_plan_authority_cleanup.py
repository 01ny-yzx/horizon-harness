"""Verify deterministic Browser execution boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.browser as browser_module
from core.browser import BrowserManager
from core.state import TaskState
from core.tool_execution_authorization import authorize_tool_execution
from core.tool_call_grants import register_tool_call_grant
from core.tool_call_schema import build_structured_tool_call_envelope
from core.tool_risk_registry import BROWSER_READ, BROWSER_WRITE, get_tool_risk_metadata
from core.tool_spec import ToolKind, ToolRisk
from tools.registry import get_local_tool_specs


SPECS = get_local_tool_specs()
def test_browser_click_risk_and_authorization() -> None:
    read_metadata = get_tool_risk_metadata("browser_extract_text")
    click_metadata = get_tool_risk_metadata("browser_click_and_extract")
    assert read_metadata is not None and read_metadata.operation == BROWSER_READ
    assert read_metadata.side_effect is False
    assert click_metadata is not None and click_metadata.operation == BROWSER_WRITE
    assert click_metadata.side_effect is True
    assert click_metadata.idempotency_policy == "task_exact"
    assert SPECS["browser_click_and_extract"].kind == ToolKind.BROWSER_WRITE
    assert SPECS["browser_click_and_extract"].risk == ToolRisk.BROWSER_SIDE_EFFECT

    state = TaskState.create(user_goal="Interact", task_type="simple", plan=[])
    arguments = {"url": "https://example.com", "selector": "#next"}
    envelope = build_structured_tool_call_envelope(
        SimpleNamespace(
            id="actual-browser-call",
            function=SimpleNamespace(
                name="browser_click_and_extract",
                arguments=json.dumps(arguments),
            ),
        )
    )
    envelope.metadata["execution_grant_required"] = True
    envelope.metadata["grant_registered_arguments"] = dict(arguments)
    grant = register_tool_call_grant(
        state,
        call_id=envelope.call_id,
        provider_call_id=envelope.provider_call_id,
        canonical_name=envelope.canonical_name,
        executable_name=envelope.executable_name,
        arguments=envelope.parsed_arguments,
        source=envelope.source,
        raw_arguments=envelope.raw_arguments,
    )
    assert grant.allowed is True
    previous = os.environ.get("AGENT_ACCESS_MODE")
    try:
        os.environ["AGENT_ACCESS_MODE"] = "read_only"
        assert authorize_tool_execution(
            task_state=state,
            tool_name="browser_click_and_extract",
            tool_call_envelope=envelope,
            sanitized_arguments=arguments,
        ).allowed is False

        os.environ["AGENT_ACCESS_MODE"] = "full_access"
        assert authorize_tool_execution(
            task_state=state,
            tool_name="browser_click_and_extract",
            tool_call_envelope=envelope,
            sanitized_arguments=arguments,
        ).allowed is True

    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous


class _Policy:
    max_text_chars = 10_000

    def check_url(self, _url: str) -> dict[str, bool]:
        return {"allowed": True}

    def check_step_count(self, _count: int) -> dict[str, bool]:
        return {"allowed": True}

    def check_form_fields(self, _fields: dict[str, Any], *, submit_selector: str | None = None) -> dict[str, bool]:
        del submit_selector
        return {"allowed": True}

    def clamp_text(self, text: str, max_chars: int | None = None) -> tuple[str, bool]:
        limit = max_chars or self.max_text_chars
        return text[:limit], len(text) > limit


class _Request:
    def is_navigation_request(self) -> bool:
        return True


class _Response:
    def __init__(self, status: int, frame: object | None = None) -> None:
        self.status = status
        self.frame = frame
        self.request = _Request()


class _Locator:
    def __init__(self, page: "_Page") -> None:
        self.page = page

    @property
    def first(self) -> "_Locator":
        return self

    def click(self, **_kwargs: Any) -> None:
        self.page.navigate_during_action()

    def fill(self, _value: str, **_kwargs: Any) -> None:
        return None

    def inner_text(self, **_kwargs: Any) -> str:
        return self.page.body


class _Page:
    def __init__(self, initial_status: int, final_url: str, action_statuses: list[int]) -> None:
        self.initial_status = initial_status
        self.url = "https://example.com/a"
        self.final_url = final_url
        self.action_statuses = action_statuses
        self.main_frame = object()
        self.body = "final body"
        self._listeners: list[Callable[[Any], None]] = []

    def set_default_timeout(self, _timeout: int) -> None:
        return None

    def goto(self, _url: str, **_kwargs: Any) -> _Response:
        return _Response(self.initial_status, self.main_frame)

    def on(self, event: str, callback: Callable[[Any], None]) -> None:
        assert event == "response"
        self._listeners.append(callback)

    def remove_listener(self, event: str, callback: Callable[[Any], None]) -> None:
        assert event == "response"
        self._listeners.remove(callback)

    def navigate_during_action(self) -> None:
        self.url = self.final_url
        for status in self.action_statuses:
            response = _Response(status, self.main_frame)
            for callback in list(self._listeners):
                callback(response)

    def locator(self, _selector: str) -> _Locator:
        return _Locator(self)

    def get_by_text(self, _text: str, **_kwargs: Any) -> _Locator:
        return _Locator(self)

    def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def title(self) -> str:
        return "Final"


def _run_browser(page: _Page, call: Callable[[BrowserManager], dict[str, Any]]) -> dict[str, Any]:
    class Context:
        def new_page(self) -> _Page:
            return page

        def close(self) -> None:
            return None

    class Browser:
        def new_context(self, **_kwargs: Any) -> Context:
            return Context()

        def close(self) -> None:
            return None

    class Scope:
        def __enter__(self) -> Any:
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: Browser()))

        def __exit__(self, *_args: Any) -> None:
            return None

    original = browser_module.sync_playwright
    browser_module.sync_playwright = lambda: Scope()
    try:
        return call(BrowserManager(policy=_Policy()))
    finally:
        browser_module.sync_playwright = original


def test_browser_current_document_status() -> None:
    stable = _run_browser(
        _Page(404, "https://example.com/a", []),
        lambda manager: manager._with_page("https://example.com/a", lambda _page: {"url": _page.url}),
    )
    assert stable["data"]["http_status"] == 404

    clicked = _run_browser(
        _Page(200, "https://example.com/b", [201]),
        lambda manager: manager.click_and_extract("https://example.com/a", selector="#next"),
    )
    assert clicked["data"]["url"] == "https://example.com/b"
    assert clicked["data"]["http_status"] == 201

    submitted = _run_browser(
        _Page(200, "https://example.com/form-result", [302, 200]),
        lambda manager: manager.fill_form(
            "https://example.com/a",
            fields={"#name": "Ada"},
            submit_selector="#submit",
        ),
    )
    assert submitted["data"]["url"] == "https://example.com/form-result"
    assert submitted["data"]["http_status"] == 200

    spa = _run_browser(
        _Page(200, "https://example.com/spa", []),
        lambda manager: manager.click_and_extract("https://example.com/a", selector="#route"),
    )
    assert spa["data"]["url"] == "https://example.com/spa"
    assert spa["data"]["http_status"] is None


def main() -> None:
    test_browser_click_risk_and_authorization()
    test_browser_current_document_status()
    print("Browser boundary smoke passed.")


if __name__ == "__main__":
    main()
