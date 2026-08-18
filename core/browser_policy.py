"""Safety policy for Browser Tools / Playwright."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from config.settings import settings


SENSITIVE_FIELD_MARKERS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "api_key",
    "apikey",
    "secret",
    "credit_card",
    "creditcard",
    "cardnumber",
    "cvv",
    "cvc",
    "otp",
    "captcha",
    "verification",
    "bank",
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "code": self.code}


class BrowserPolicy:
    """Validate browser actions before Playwright is allowed to run."""

    def __init__(self) -> None:
        self.max_steps = max(1, int(settings.browser_max_steps or 8))
        self.max_text_chars = max(500, int(settings.browser_max_text_chars or 12000))
        self.screenshot_enabled = bool(settings.browser_screenshot_enabled)

    def check_url(self, url: str) -> dict[str, Any]:
        if not settings.browser_enabled:
            return PolicyDecision(False, "Browser Tools are disabled by BROWSER_ENABLED.", "browser_disabled").to_dict()
        if not url or not url.strip():
            return PolicyDecision(False, "URL cannot be empty.", "empty_url").to_dict()
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        if scheme in {"file", "chrome", "edge", "about"}:
            return PolicyDecision(False, f"{scheme} URLs are not allowed.", "blocked_scheme").to_dict()
        if scheme not in {"http", "https"}:
            return PolicyDecision(False, "Only http and https URLs are allowed.", "unsupported_scheme").to_dict()
        if not parsed.hostname:
            return PolicyDecision(False, "URL hostname is required.", "missing_hostname").to_dict()
        if not settings.browser_allow_external:
            return PolicyDecision(False, "External browser access is disabled.", "external_disabled").to_dict()

        host = parsed.hostname.lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".localhost"):
            return PolicyDecision(False, "Localhost URLs are not allowed.", "blocked_localhost").to_dict()

        literal_ip = self._parse_ip(host)
        if literal_ip and self._is_private_or_local_ip(literal_ip):
            return PolicyDecision(False, "Private or local IP addresses are not allowed.", "blocked_private_ip").to_dict()
        if literal_ip is None:
            dns_decision = self._check_dns_resolved_ips(host)
            if dns_decision is not None:
                return dns_decision.to_dict()

        return PolicyDecision(True, "URL is allowed.", "allowed").to_dict()

    def check_step_count(self, steps: int) -> dict[str, Any]:
        if steps > self.max_steps:
            return PolicyDecision(False, "Browser task step limit exceeded.", "max_steps_exceeded").to_dict()
        return PolicyDecision(True, "Step count is allowed.", "allowed").to_dict()

    def check_screenshot(self) -> dict[str, Any]:
        if not self.screenshot_enabled:
            return PolicyDecision(False, "Screenshots are disabled by BROWSER_SCREENSHOT_ENABLED.", "screenshot_disabled").to_dict()
        return PolicyDecision(True, "Screenshot is allowed.", "allowed").to_dict()

    def check_form_fields(self, fields: dict[str, Any], submit_selector: str | None = None) -> dict[str, Any]:
        for key in fields:
            normalized = re.sub(r"[^a-z0-9_]+", "_", str(key).lower())
            if any(marker in normalized for marker in SENSITIVE_FIELD_MARKERS):
                return PolicyDecision(False, "Sensitive form fields must be handled manually.", "sensitive_form_field").to_dict()
        if submit_selector:
            lowered = submit_selector.lower()
            if any(marker in lowered for marker in {"password", "pay", "card", "captcha", "otp"}):
                return PolicyDecision(False, "Sensitive form submission is not allowed.", "sensitive_form_submit").to_dict()
        return PolicyDecision(True, "Form fields are allowed.", "allowed").to_dict()

    def clamp_text(self, text: str, max_chars: int | None = None) -> tuple[str, bool]:
        limit = max(1, min(int(max_chars or self.max_text_chars), self.max_text_chars))
        return (text[:limit], len(text) > limit)

    @staticmethod
    def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    @staticmethod
    def _is_private_or_local_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    def _check_dns_resolved_ips(self, host: str) -> PolicyDecision | None:
        try:
            resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            return PolicyDecision(False, "DNS resolution failed for browser URL host.", "blocked_dns_resolution_failed")
        for item in resolved:
            sockaddr = item[4]
            if not sockaddr:
                continue
            ip = self._parse_ip(str(sockaddr[0]))
            if ip and self._is_private_or_local_ip(ip):
                return PolicyDecision(False, "DNS resolved to a private or local IP address.", "blocked_dns_private_ip")
        return None
