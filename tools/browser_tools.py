"""Tool wrappers for Browser Tools / Playwright v1."""

from __future__ import annotations

from typing import Any

from core.browser import BrowserManager


def get_browser_status() -> dict[str, Any]:
    return BrowserManager().get_status()


def browser_extract_text(url: str, timeout_ms: int | None = None, max_chars: int | None = None) -> dict[str, Any]:
    return BrowserManager().extract_text(url, max_chars=max_chars, timeout_ms=timeout_ms)


def browser_screenshot(url: str, full_page: bool = False) -> dict[str, Any]:
    return BrowserManager().screenshot(url, full_page=full_page)


def browser_list_links(url: str) -> dict[str, Any]:
    return BrowserManager().list_links(url)


def browser_click_and_extract(url: str, selector: str | None = None, text: str | None = None) -> dict[str, Any]:
    return BrowserManager().click_and_extract(url, selector=selector, text=text)


def browser_fill_form(url: str, fields: dict[str, Any], submit_selector: str | None = None) -> dict[str, Any]:
    return BrowserManager().fill_form(url, fields=fields, submit_selector=submit_selector)


BROWSER_TOOLS = {
    "get_browser_status": get_browser_status,
    "browser_extract_text": browser_extract_text,
    "browser_screenshot": browser_screenshot,
    "browser_list_links": browser_list_links,
    "browser_click_and_extract": browser_click_and_extract,
    "browser_fill_form": browser_fill_form,
}


BROWSER_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_browser_status",
            "description": "Check Browser Tools / Playwright status and installation hints.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract_text",
            "description": "Use Playwright Chromium to open an explicit public HTTP(S) URL and extract visible text from the rendered page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Public http or https URL."},
                    "timeout_ms": {"type": "integer", "description": "Optional timeout in milliseconds, capped at 30000."},
                    "max_chars": {"type": "integer", "description": "Optional text character limit, capped by BROWSER_MAX_TEXT_CHARS."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a screenshot of a public page and return the saved file path, not image bytes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "full_page": {"type": "boolean", "default": False},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_list_links",
            "description": "Use Playwright to list links from a public page.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click_and_extract",
            "description": "Open a public page, click a CSS selector or visible text, then extract visible text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill_form",
            "description": "Carefully fill non-sensitive form fields on a public page. Passwords, tokens, cards, OTP, and captcha fields are refused.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "fields": {"type": "object"},
                    "submit_selector": {"type": "string"},
                },
                "required": ["url", "fields"],
            },
        },
    },
]
