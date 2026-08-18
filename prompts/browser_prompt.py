"""Prompt rules for Browser Tools."""

from __future__ import annotations


def build_browser_prompt() -> str:
    """Return Browser Tools usage and safety rules."""

    return """
Browser Tools / Playwright v1 rules:
1. Browser tools may be used only when they are present in the current tool surface. If Browser tools are unavailable, do not claim browser access.
2. Browser Tools are for explicit URLs, dynamic pages, screenshots, clicking, link extraction, and careful non-sensitive form filling.
3. The current tool surface and scoped schemas determine which Browser capabilities are available and callable. Choose among the available capabilities from the task and existing Observations.
4. Browser Tools do not replace local documents or RAG.
5. Do not claim an unread page was accessed.
6. Do not open file://, localhost, local network, private IP, chrome://, edge://, or about: URLs.
7. Do not claim a page body was read unless the browser tool returned success and extraction_quality is not empty. If Browser Tools fail, use error_code, message, suggestion, category, and retryable to explain the failure plainly.
8. Do not return cookies, localStorage, sessionStorage, browser storage, API keys, tokens, passwords, secrets, traceback, or long page dumps. Browser text must include the URL and stay bounded.
9. Do not automatically handle passwords, tokens, payment, card numbers, bank details, captcha, OTP, or sensitive login flows. Ask the user to handle those manually.
10. Screenshots return a saved path only; never embed binary screenshot data in tool output.
11. Browser sessions are temporary. Do not rely on saved login state or persistent cookies.
12. If Browser fails, clearly state the friendly failure reason and do not expose raw exception stacks or sensitive values.
13. Browser observations may include title, final_url, meta_description, headings, summary, bounded text, links, links_count, and extraction_quality. Deterministic summary is only a short aid; do not treat it as new facts beyond the extracted page.
""".strip()
