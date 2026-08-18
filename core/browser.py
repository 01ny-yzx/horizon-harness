"""Playwright-backed browser helpers with no persistent session state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config.settings import settings
from core.browser_errors import (
    BrowserErrorCode,
    classify_browser_error,
    format_browser_error,
    format_policy_error,
    format_timeout_error,
)
from core.browser_policy import BrowserPolicy
from core.browser_result_ux import (
    extract_page_metadata,
    format_browser_extract_result,
    format_links_result,
    safe_screenshot_path,
)

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - dependency may be intentionally absent.
    PlaywrightError = Exception  # type: ignore[assignment]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment]
    sync_playwright = None  # type: ignore[assignment]


INSTALL_PLAYWRIGHT = "请在当前虚拟环境中运行：python -m pip install -r requirements.txt"
INSTALL_CHROMIUM = "请运行：python -m playwright install chromium"


def _response_status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _main_frame_navigation_status(page: Any, response: Any) -> int | None:
    if getattr(response, "frame", None) is not getattr(page, "main_frame", None):
        return None
    request = getattr(response, "request", None)
    is_navigation_request = getattr(request, "is_navigation_request", None)
    try:
        is_navigation = (
            is_navigation_request()
            if callable(is_navigation_request)
            else bool(is_navigation_request)
        )
    except Exception:  # noqa: BLE001
        return None
    return _response_status(response) if is_navigation else None


class BrowserManager:
    """One-shot browser operations using fresh contexts for every call."""

    def __init__(self, policy: BrowserPolicy | None = None) -> None:
        self.policy = policy or BrowserPolicy()

    def get_status(self) -> dict[str, Any]:
        playwright_installed = sync_playwright is not None
        chromium_available = False
        browser_channel = _configured_browser_channel()
        using_system_browser = bool(browser_channel)
        diagnostic: dict[str, Any] | None = None
        if not settings.browser_enabled:
            diagnostic = format_browser_error(BrowserErrorCode.DISABLED)
        elif not playwright_installed:
            diagnostic = format_browser_error(BrowserErrorCode.PLAYWRIGHT_MISSING)
        else:
            try:
                with sync_playwright() as p:
                    browser = self._launch_browser(p, headless=True)
                    browser.close()
                chromium_available = True
            except Exception as exc:  # noqa: BLE001
                diagnostic = classify_browser_error(exc, operation="launch", browser_channel=browser_channel)
        success = bool(settings.browser_enabled and playwright_installed and chromium_available)
        data = {
            "enabled": settings.browser_enabled,
            "backend": "playwright",
            "playwright_installed": playwright_installed,
            "chromium_available": chromium_available,
            "browser_channel": settings.BROWSER_CHANNEL,
            "using_system_browser": bool(settings.BROWSER_CHANNEL),
            "install_hint": _install_hint(playwright_installed, chromium_available),
            "headless": settings.browser_headless,
            "screenshot_enabled": settings.browser_screenshot_enabled,
            "max_steps": self.policy.max_steps,
            "timeout_ms": settings.browser_timeout_ms,
        }
        if diagnostic:
            data["diagnostic"] = {key: value for key, value in diagnostic.items() if key != "success"}
        result = {
            "success": success,
            "data": data,
            "error": diagnostic.get("error", "") if diagnostic else "",
        }
        if diagnostic:
            result.update(
                {
                    "error_code": diagnostic.get("error_code"),
                    "message": diagnostic.get("message"),
                    "suggestion": diagnostic.get("suggestion"),
                    "category": diagnostic.get("category"),
                    "retryable": diagnostic.get("retryable"),
                }
            )
        return {
            **result,
        }

    def open_page(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        action = _mark_operation(lambda page: self._open_page_data(page), "extraction")
        return self._with_page(url, action, wait_until=wait_until)

    def extract_text(self, url: str, max_chars: int | None = None, timeout_ms: int | None = None) -> dict[str, Any]:
        effective_timeout = _safe_timeout(timeout_ms)

        def action(page: Any) -> dict[str, Any]:
            snapshot = extract_page_metadata(page, url, effective_timeout)
            return format_browser_extract_result(snapshot, max_chars or self.policy.max_text_chars)

        return self._with_page(url, _mark_operation(action, "extraction"), timeout_ms=effective_timeout)

    def screenshot(self, url: str, full_page: bool = False) -> dict[str, Any]:
        decision = self.policy.check_screenshot()
        if not decision["allowed"]:
            return format_policy_error(decision, url=url)

        def action(page: Any) -> dict[str, Any]:
            path_data = safe_screenshot_path(str(page.url), settings.browser_screenshot_dir)
            try:
                page.screenshot(path=path_data["screenshot_path"], full_page=bool(full_page))
            except Exception as exc:  # noqa: BLE001
                raise BrowserOperationError("screenshot", exc) from exc
            return {
                "title": page.title(),
                "url": url,
                "final_url": page.url,
                **path_data,
                "full_page": bool(full_page),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        return self._with_page(url, action)

    def click_and_extract(self, url: str, selector: str | None = None, text: str | None = None) -> dict[str, Any]:
        if not selector and not text:
            return format_browser_error(
                BrowserErrorCode.TOOL_FAILED,
                message="缺少点击目标。",
                suggestion="请提供 selector 或 text。",
            )
        step_check = self.policy.check_step_count(2)
        if not step_check["allowed"]:
            return format_policy_error(step_check, url=url)

        def action(page: Any) -> dict[str, Any]:
            if selector:
                page.locator(selector).first.click(timeout=settings.browser_timeout_ms)
            else:
                page.get_by_text(str(text), exact=False).first.click(timeout=settings.browser_timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=settings.browser_timeout_ms)
            body = _normalize_whitespace(page.locator("body").inner_text(timeout=settings.browser_timeout_ms))
            body, truncated = self.policy.clamp_text(body)
            return {"title": page.title(), "url": page.url, "text": body, "text_preview": body[:1000], "truncated": truncated}

        return self._with_page(url, _mark_operation(action, "extraction"))

    def fill_form(self, url: str, fields: dict[str, Any], submit_selector: str | None = None) -> dict[str, Any]:
        if not isinstance(fields, dict) or not fields:
            return format_browser_error(
                BrowserErrorCode.TOOL_FAILED,
                message="表单字段不能为空。",
                suggestion="请提供非空 fields 对象。",
            )
        decision = self.policy.check_form_fields(fields, submit_selector=submit_selector)
        if not decision["allowed"]:
            return format_policy_error(decision, url=url)

        def action(page: Any) -> dict[str, Any]:
            filled: list[str] = []
            for selector, value in fields.items():
                page.locator(str(selector)).first.fill(str(value), timeout=settings.browser_timeout_ms)
                filled.append(str(selector))
            submitted = False
            if submit_selector:
                page.locator(submit_selector).first.click(timeout=settings.browser_timeout_ms)
                page.wait_for_load_state("domcontentloaded", timeout=settings.browser_timeout_ms)
                submitted = True
            body = _normalize_whitespace(page.locator("body").inner_text(timeout=settings.browser_timeout_ms))
            body, truncated = self.policy.clamp_text(body)
            return {
                "title": page.title(),
                "url": page.url,
                "filled_fields": filled,
                "submitted": submitted,
                "text_preview": body[:1000],
                "truncated": truncated,
            }

        return self._with_page(url, _mark_operation(action, "extraction"))

    def list_links(self, url: str) -> dict[str, Any]:
        def action(page: Any) -> dict[str, Any]:
            snapshot = extract_page_metadata(page, url, settings.browser_timeout_ms)
            return format_links_result(snapshot)

        return self._with_page(url, _mark_operation(action, "extraction"))

    def _open_page_data(self, page: Any) -> dict[str, Any]:
        text = _normalize_whitespace(page.locator("body").inner_text(timeout=settings.browser_timeout_ms))
        preview, truncated = self.policy.clamp_text(text, max_chars=1000)
        links_count = page.locator("a").count()
        return {"title": page.title(), "url": page.url, "text_preview": preview, "truncated": truncated, "links_count": links_count}

    def _with_page(
        self,
        url: str,
        action: Any,
        wait_until: str = "domcontentloaded",
        timeout_ms: int | None = None,
        operation: str = "navigation",
    ) -> dict[str, Any]:
        effective_timeout = _safe_timeout(timeout_ms)
        effective_operation = str(getattr(action, "_browser_operation", operation) or operation)
        decision = self.policy.check_url(url)
        if not decision["allowed"]:
            return format_policy_error(decision, url=url)
        if sync_playwright is None:
            return format_browser_error(BrowserErrorCode.PLAYWRIGHT_MISSING, url=url)
        browser = None
        context = None
        try:
            with sync_playwright() as p:
                try:
                    browser = self._launch_browser(p)
                except Exception as exc:  # noqa: BLE001
                    return classify_browser_error(exc, operation="launch", browser_channel=_configured_browser_channel())
                context_kwargs: dict[str, Any] = {}
                if settings.browser_user_agent:
                    context_kwargs["user_agent"] = settings.browser_user_agent
                try:
                    context = browser.new_context(**context_kwargs)
                except Exception as exc:  # noqa: BLE001
                    return classify_browser_error(exc, operation="context", browser_channel=_configured_browser_channel())
                try:
                    page = context.new_page()
                except Exception as exc:  # noqa: BLE001
                    return classify_browser_error(exc, operation="page", browser_channel=_configured_browser_channel())
                page.set_default_timeout(effective_timeout)
                initial_response = page.goto(
                    url,
                    wait_until=wait_until,
                    timeout=effective_timeout,
                )
                initial_url = str(page.url)
                initial_status = _response_status(initial_response)
                action_navigation_statuses: list[int] = []

                def record_action_navigation(response: Any) -> None:
                    status = _main_frame_navigation_status(page, response)
                    if status is not None:
                        action_navigation_statuses.append(status)

                listener_added = False
                try:
                    page.on("response", record_action_navigation)
                    listener_added = True
                    data = action(page)
                except BrowserOperationError as exc:
                    if isinstance(exc.original, PlaywrightTimeoutError):
                        return format_timeout_error(effective_timeout)
                    return classify_browser_error(exc.original, operation=exc.operation, timeout_ms=effective_timeout)
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc, PlaywrightTimeoutError):
                        return format_timeout_error(effective_timeout)
                    return _operation_error(effective_operation, exc, effective_timeout)
                finally:
                    if listener_added:
                        page.remove_listener("response", record_action_navigation)
                if isinstance(data, dict):
                    final_url = str(page.url)
                    data["http_status"] = (
                        action_navigation_statuses[-1]
                        if action_navigation_statuses
                        else initial_status
                        if final_url == initial_url
                        else None
                    )
                return {"success": True, "data": data}
        except PlaywrightTimeoutError:
            return format_timeout_error(effective_timeout)
        except Exception as exc:  # noqa: BLE001
            return classify_browser_error(exc, operation="navigation", timeout_ms=effective_timeout, browser_channel=_configured_browser_channel())
        finally:
            if context is not None:
                _safe_close(context)
            if browser is not None:
                _safe_close(browser)

    @staticmethod
    def _launch_browser(playwright: Any, headless: bool | None = None) -> Any:
        channel = _configured_browser_channel()
        launch_kwargs: dict[str, Any] = {
            "headless": settings.browser_headless if headless is None else headless,
            "timeout": settings.browser_timeout_ms,
        }
        if channel:
            launch_kwargs["channel"] = channel
        return playwright.chromium.launch(**launch_kwargs)


def _normalize_whitespace(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _safe_timeout(timeout_ms: int | None) -> int:
    if timeout_ms is None:
        return settings.browser_timeout_ms
    try:
        value = int(timeout_ms)
    except (TypeError, ValueError):
        return settings.browser_timeout_ms
    return max(1_000, min(value, 30_000))


def _configured_browser_channel() -> str:
    channel = str(settings.BROWSER_CHANNEL or settings.browser_channel or "").strip().lower()
    return channel


def _install_hint(playwright_installed: bool, chromium_available: bool) -> str:
    if not playwright_installed:
        return INSTALL_PLAYWRIGHT
    if not chromium_available:
        return INSTALL_CHROMIUM
    return ""


class BrowserOperationError(Exception):
    def __init__(self, operation: str, original: Exception) -> None:
        super().__init__(str(original))
        self.operation = operation
        self.original = original


def _operation_error(operation: str, exc: Exception, timeout_ms: int) -> dict[str, Any]:
    result = classify_browser_error(exc, operation=operation, timeout_ms=timeout_ms)
    if operation == "extraction":
        result["data"] = {
            "extraction_quality": "error",
            "read_method": "browser_extract_text",
        }
    return result


def _safe_close(resource: Any) -> None:
    try:
        resource.close()
    except Exception:  # noqa: BLE001
        return


def _mark_operation(action: Any, operation: str) -> Any:
    try:
        setattr(action, "_browser_operation", operation)
    except Exception:  # noqa: BLE001
        return action
    return action
