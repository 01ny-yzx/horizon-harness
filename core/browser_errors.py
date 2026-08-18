"""Friendly Browser MCP errors and redaction helpers."""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BrowserErrorCode(StrEnum):
    DISABLED = "browser_disabled"
    BLOCKED_FILE_URL = "browser_blocked_file_url"
    BLOCKED_LOCALHOST = "browser_blocked_localhost"
    BLOCKED_PRIVATE_IP = "browser_blocked_private_ip"
    BLOCKED_SCHEME = "browser_blocked_scheme"
    EMPTY_URL = "browser_empty_url"
    MISSING_HOSTNAME = "browser_missing_hostname"
    EXTERNAL_DISABLED = "browser_external_disabled"
    SCREENSHOT_DISABLED = "browser_screenshot_disabled"
    SENSITIVE_FORM_FIELD = "browser_sensitive_form_field"
    SENSITIVE_FORM_SUBMIT = "browser_sensitive_form_submit"
    MAX_STEPS_EXCEEDED = "browser_max_steps_exceeded"
    PLAYWRIGHT_MISSING = "browser_playwright_missing"
    CHROMIUM_MISSING = "browser_chromium_missing"
    TIMEOUT = "browser_timeout"
    DNS_FAILED = "browser_dns_failed"
    NETWORK_UNREACHABLE = "browser_network_unreachable"
    CONNECTION_REFUSED = "browser_connection_refused"
    CONNECTION_TIMED_OUT = "browser_connection_timed_out"
    CONNECTION_RESET = "browser_connection_reset"
    NAVIGATION_FAILED = "browser_navigation_failed"
    LAUNCH_FAILED = "browser_launch_failed"
    CONTEXT_FAILED = "browser_context_failed"
    PAGE_FAILED = "browser_page_failed"
    EXTRACTION_FAILED = "browser_extraction_failed"
    SCREENSHOT_FAILED = "browser_screenshot_failed"
    TOOL_NOT_FOUND = "browser_tool_not_found"
    TOOL_FAILED = "browser_tool_failed"
    INTERNAL_ERROR = "browser_internal_error"


@dataclass(frozen=True)
class BrowserFriendlyError:
    error_code: str
    message: str
    suggestion: str
    category: str
    retryable: bool = False
    details: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": False,
            "error": self.message,
            "error_code": self.error_code,
            "message": self.message,
            "suggestion": self.suggestion,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.url:
            payload["url"] = self.url
        if self.details:
            payload["details"] = redact_browser_error(self.details)
        if self.data:
            payload["data"] = redact_browser_error(self.data)
        return payload


POLICY_CODE_MAP = {
    "browser_disabled": BrowserErrorCode.DISABLED,
    "blocked_scheme": BrowserErrorCode.BLOCKED_SCHEME,
    "unsupported_scheme": BrowserErrorCode.BLOCKED_SCHEME,
    "empty_url": BrowserErrorCode.EMPTY_URL,
    "missing_hostname": BrowserErrorCode.MISSING_HOSTNAME,
    "external_disabled": BrowserErrorCode.EXTERNAL_DISABLED,
    "blocked_localhost": BrowserErrorCode.BLOCKED_LOCALHOST,
    "blocked_private_ip": BrowserErrorCode.BLOCKED_PRIVATE_IP,
    "screenshot_disabled": BrowserErrorCode.SCREENSHOT_DISABLED,
    "sensitive_form_field": BrowserErrorCode.SENSITIVE_FORM_FIELD,
    "sensitive_form_submit": BrowserErrorCode.SENSITIVE_FORM_SUBMIT,
    "max_steps_exceeded": BrowserErrorCode.MAX_STEPS_EXCEEDED,
}

FRIENDLY_TEXT = {
    BrowserErrorCode.DISABLED: (
        "浏览器工具当前已禁用。",
        "请确认 BROWSER_ENABLED 已开启后再试。",
        "policy",
        False,
    ),
    BrowserErrorCode.BLOCKED_FILE_URL: (
        "无法访问 file:// 本地文件地址。浏览器工具只允许访问公开 http/https 网页。",
        "请改用公开的 http 或 https 地址。",
        "policy",
        False,
    ),
    BrowserErrorCode.BLOCKED_LOCALHOST: (
        "无法访问 localhost 或 127.0.0.1。为安全起见，浏览器工具不会访问本机服务。",
        "请改用公开的 http 或 https 地址。",
        "policy",
        False,
    ),
    BrowserErrorCode.BLOCKED_PRIVATE_IP: (
        "无法访问私有 IP 地址。为安全起见，浏览器工具只访问公开网页。",
        "请改用公开可访问的 http 或 https 地址。",
        "policy",
        False,
    ),
    BrowserErrorCode.BLOCKED_SCHEME: (
        "无法访问这个地址类型。浏览器工具只允许公开 http/https 网页。",
        "请使用公开的 http 或 https 地址。",
        "policy",
        False,
    ),
    BrowserErrorCode.EMPTY_URL: ("网址不能为空。", "请提供完整的 http 或 https 地址。", "policy", False),
    BrowserErrorCode.MISSING_HOSTNAME: ("网址缺少域名。", "请检查 URL 是否完整。", "policy", False),
    BrowserErrorCode.EXTERNAL_DISABLED: (
        "浏览器外部访问当前已禁用。",
        "请确认 BROWSER_ALLOW_EXTERNAL 已开启后再试。",
        "policy",
        False,
    ),
    BrowserErrorCode.SCREENSHOT_DISABLED: (
        "截图功能当前已禁用。",
        "请开启 BROWSER_SCREENSHOT_ENABLED 后再试。",
        "policy",
        False,
    ),
    BrowserErrorCode.SENSITIVE_FORM_FIELD: (
        "这个表单字段看起来包含密码、token、验证码或支付信息，浏览器工具不会自动填写。",
        "请手动处理敏感字段，或只填写非敏感字段。",
        "policy",
        False,
    ),
    BrowserErrorCode.SENSITIVE_FORM_SUBMIT: (
        "这个表单提交看起来涉及密码、验证码或支付信息，浏览器工具不会自动提交。",
        "请手动完成敏感提交。",
        "policy",
        False,
    ),
    BrowserErrorCode.MAX_STEPS_EXCEEDED: (
        "浏览器任务步骤数超过限制。",
        "请减少点击/操作步骤，或调整 BROWSER_MAX_STEPS。",
        "policy",
        False,
    ),
    BrowserErrorCode.PLAYWRIGHT_MISSING: (
        "浏览器依赖 Playwright 未安装。",
        "请在当前虚拟环境中运行：python -m pip install -r requirements.txt",
        "dependency",
        False,
    ),
    BrowserErrorCode.CHROMIUM_MISSING: (
        "Playwright 已安装，但 Chromium 浏览器未安装。",
        "请运行：python -m playwright install chromium",
        "dependency",
        False,
    ),
    BrowserErrorCode.TIMEOUT: (
        "网页加载或浏览器操作超时。",
        "请稍后重试，或增加 BROWSER_TIMEOUT_MS。如果网页需要登录、验证码或很慢，浏览器工具可能无法读取。",
        "timeout",
        True,
    ),
    BrowserErrorCode.DNS_FAILED: ("无法解析这个域名。", "请确认网址拼写和网络连接。", "network", True),
    BrowserErrorCode.NETWORK_UNREACHABLE: ("当前网络无法连接到目标网站。", "请确认网络连接后重试。", "network", True),
    BrowserErrorCode.CONNECTION_REFUSED: ("无法连接到目标网站。", "请确认网站是否可访问。", "network", True),
    BrowserErrorCode.CONNECTION_TIMED_OUT: ("网页连接超时。", "请稍后重试。", "network", True),
    BrowserErrorCode.CONNECTION_RESET: ("网页连接被中断。", "请稍后重试。", "network", True),
    BrowserErrorCode.NAVIGATION_FAILED: (
        "浏览器打开网页失败。",
        "请确认网址可访问；如果页面需要登录、验证码或特殊权限，浏览器工具可能无法读取。",
        "network",
        True,
    ),
    BrowserErrorCode.LAUNCH_FAILED: (
        "浏览器启动失败。",
        "请检查 Playwright / Chromium 是否已安装，或检查 BROWSER_CHANNEL 对应的系统浏览器。",
        "runtime",
        False,
    ),
    BrowserErrorCode.CONTEXT_FAILED: (
        "浏览器上下文创建失败。",
        "请检查浏览器安装和运行环境后重试。",
        "runtime",
        False,
    ),
    BrowserErrorCode.PAGE_FAILED: (
        "浏览器页面创建失败。",
        "请检查浏览器安装和运行环境后重试。",
        "runtime",
        False,
    ),
    BrowserErrorCode.EXTRACTION_FAILED: (
        "页面内容读取失败。",
        "请换一个 URL，或检查页面是否需要登录、验证码或 JavaScript 权限。",
        "extraction",
        True,
    ),
    BrowserErrorCode.SCREENSHOT_FAILED: (
        "截图失败。",
        "请稍后重试，或检查截图目录是否可写。",
        "screenshot",
        True,
    ),
    BrowserErrorCode.TOOL_NOT_FOUND: ("未知的 Browser MCP 工具。", "请使用 tools/list 返回的工具名。", "mcp", False),
    BrowserErrorCode.TOOL_FAILED: ("Browser MCP 工具执行失败。", "请检查参数后重试。", "mcp", False),
    BrowserErrorCode.INTERNAL_ERROR: ("Browser MCP 请求处理失败。", "请稍后重试；如仍失败，请查看调试日志。", "mcp", False),
}


def format_browser_error(
    code: BrowserErrorCode | str,
    *,
    url: str | None = None,
    details: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    message: str | None = None,
    suggestion: str | None = None,
    category: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    normalized = BrowserErrorCode(str(code)) if str(code) in BrowserErrorCode._value2member_map_ else BrowserErrorCode.INTERNAL_ERROR
    default_message, default_suggestion, default_category, default_retryable = FRIENDLY_TEXT[normalized]
    friendly = BrowserFriendlyError(
        error_code=str(normalized),
        message=message or default_message,
        suggestion=suggestion or default_suggestion,
        category=category or default_category,
        retryable=default_retryable if retryable is None else bool(retryable),
        details=details,
        data=data,
        url=url,
    )
    return friendly.to_dict()


def format_policy_error(decision: dict[str, Any], *, url: str | None = None) -> dict[str, Any]:
    raw_code = str(decision.get("code") or "")
    if raw_code == "blocked_scheme" and str(url or "").strip().lower().startswith("file:"):
        code = BrowserErrorCode.BLOCKED_FILE_URL
    else:
        code = POLICY_CODE_MAP.get(raw_code, BrowserErrorCode.BLOCKED_SCHEME)
    return format_browser_error(code, url=url, data={"policy": decision})


def classify_browser_error(
    exc: BaseException | str,
    *,
    operation: str = "navigation",
    timeout_ms: int | None = None,
    browser_channel: str = "",
) -> dict[str, Any]:
    text = str(exc) if not isinstance(exc, str) else exc
    lowered = text.lower()
    type_name = type(exc).__name__.lower() if not isinstance(exc, str) else ""
    details: dict[str, Any] = {}
    if timeout_ms is not None:
        details["timeout_ms"] = timeout_ms

    if "timeouterror" in type_name or (operation != "navigation" and "timeout" in lowered):
        return format_timeout_error(timeout_ms or 0)
    if "err_name_not_resolved" in lowered or "enotfound" in lowered:
        return format_browser_error(BrowserErrorCode.DNS_FAILED, details=details)
    if "err_internet_disconnected" in lowered or "network is unreachable" in lowered:
        return format_browser_error(BrowserErrorCode.NETWORK_UNREACHABLE, details=details)
    if "err_connection_refused" in lowered or "econnrefused" in lowered:
        return format_browser_error(BrowserErrorCode.CONNECTION_REFUSED, details=details)
    if "err_connection_timed_out" in lowered or "etimedout" in lowered:
        return format_browser_error(BrowserErrorCode.CONNECTION_TIMED_OUT, details=details)
    if "err_connection_reset" in lowered or "econnreset" in lowered:
        return format_browser_error(BrowserErrorCode.CONNECTION_RESET, details=details)
    if "net::err_" in lowered:
        return format_browser_error(BrowserErrorCode.NAVIGATION_FAILED, details=details)
    if operation == "launch" and _looks_like_missing_browser_text(lowered):
        if browser_channel:
            return format_browser_error(
                BrowserErrorCode.CHROMIUM_MISSING,
                message="未找到配置的浏览器 channel。",
                suggestion="请安装对应浏览器，或清空 BROWSER_CHANNEL 后安装 Playwright Chromium。",
                details={"browser_channel": browser_channel},
            )
        return format_browser_error(BrowserErrorCode.CHROMIUM_MISSING)
    if operation == "launch":
        return format_browser_error(BrowserErrorCode.LAUNCH_FAILED)
    if operation == "context":
        return format_browser_error(BrowserErrorCode.CONTEXT_FAILED)
    if operation == "page":
        return format_browser_error(BrowserErrorCode.PAGE_FAILED)
    if operation == "screenshot":
        return format_browser_error(BrowserErrorCode.SCREENSHOT_FAILED)
    if operation == "extraction":
        return format_browser_error(
            BrowserErrorCode.EXTRACTION_FAILED,
            details={"extraction_quality": "error", "read_method": "browser_extract_text"},
        )
    return format_browser_error(BrowserErrorCode.NAVIGATION_FAILED, details=details)


def format_timeout_error(timeout_ms: int) -> dict[str, Any]:
    return format_browser_error(BrowserErrorCode.TIMEOUT, details={"timeout_ms": timeout_ms})


def redact_browser_error(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): redact_browser_error(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_browser_error(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_browser_error(item) for item in value)
    if not isinstance(value, str):
        return value
    return _redact_text(value)


def safe_exception_summary(exc: BaseException, *, limit: int = 300) -> str:
    text = str(exc) or type(exc).__name__
    text = text.replace("\n", " ")
    return str(redact_browser_error(text))[:limit]


def safe_traceback(exc: BaseException, *, limit: int = 4000) -> str:
    return str(redact_browser_error("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))))[:limit]


def _looks_like_missing_browser_text(lowered: str) -> bool:
    return (
        "executable doesn't exist" in lowered
        or "playwright install" in lowered
        or "browser has not been downloaded" in lowered
    )


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)\b(password|passwd|token|api_key|apikey|secret|cookie|session|credential)\s*=\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(password|passwd|token|api_key|apikey|secret|cookie|authorization|bearer|session|credential)\s*:\s*([^\n,;]+)"),
]


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = re.sub(r"(?i)\b(localStorage|sessionStorage)\b", "[browser_storage]", redacted)
    redacted = re.sub(r"(?i)traceback \(most recent call last\):.*", "[traceback redacted]", redacted, flags=re.DOTALL)
    return redacted
