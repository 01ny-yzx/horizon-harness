"""Static desktop health status models."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable

from desktop.runtime import DesktopRuntimeState


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|password|secret|credential)\b\s*([:=])\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(authorization)\b\s*:\s*bearer\s+([^\s,;]+)"),
]


@dataclass(frozen=True)
class DesktopHealthStatus:
    ok: bool
    backend_reachable: bool = False
    model_configured: bool = False
    mcp_enabled: bool = False
    data_dir_ready: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackendHealthResult:
    ok: bool
    reachable: bool
    url: str
    status_code: int | None = None
    status: str = "unknown"
    data: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def desktop_health_to_dict(status: DesktopHealthStatus) -> dict[str, object]:
    return asdict(status)


def backend_health_result_to_dict(result: BackendHealthResult) -> dict[str, object]:
    return asdict(result)


def build_static_desktop_health(
    *,
    backend_reachable: bool = False,
    model_configured: bool = False,
    mcp_enabled: bool = False,
    data_dir_ready: bool = False,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> DesktopHealthStatus:
    safe_errors = list(errors or [])
    safe_warnings = list(warnings or [])
    return DesktopHealthStatus(
        ok=not safe_errors,
        backend_reachable=backend_reachable,
        model_configured=model_configured,
        mcp_enabled=mcp_enabled,
        data_dir_ready=data_dir_ready,
        errors=safe_errors,
        warnings=safe_warnings,
    )


def build_desktop_health_from_local_data(
    local_data_status: object,
    *,
    backend_reachable: bool = False,
    model_configured: bool = False,
    mcp_enabled: bool = False,
) -> DesktopHealthStatus:
    errors = list(getattr(local_data_status, "errors", []) or [])
    warnings = list(getattr(local_data_status, "warnings", []) or [])
    data_dir_ready = bool(getattr(local_data_status, "ok", False))
    return DesktopHealthStatus(
        ok=data_dir_ready and not errors,
        backend_reachable=backend_reachable,
        model_configured=model_configured,
        mcp_enabled=mcp_enabled,
        data_dir_ready=data_dir_ready,
        errors=errors,
        warnings=warnings,
    )


def build_health_check_url(
    *,
    host: str = "127.0.0.1",
    port: int | None,
    path: str = "/health",
) -> str | None:
    if port is None:
        return None
    safe_path = path if path.startswith("/") else f"/{path}"
    return f"http://{host}:{port}{safe_path}"


def default_http_get_json(url: str, *, timeout: float = 1.0) -> tuple[int, dict[str, object]]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        status_code = int(getattr(response, "status", response.getcode()))
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("invalid json payload")
    return status_code, data


def check_backend_health(
    *,
    host: str = "127.0.0.1",
    port: int | None,
    timeout: float = 1.0,
    http_get_json: Callable[..., tuple[int, dict[str, object]]] | None = None,
) -> BackendHealthResult:
    url = build_health_check_url(host=host, port=port)
    if url is None:
        return BackendHealthResult(ok=False, reachable=False, url="", error="missing_port")
    getter = http_get_json or default_http_get_json
    try:
        status_code, payload = getter(url, timeout=timeout)
    except Exception as exc:
        return BackendHealthResult(
            ok=False,
            reachable=False,
            url=url,
            error="unreachable",
            warnings=[sanitize_health_error(exc)],
        )
    if not 200 <= status_code < 300:
        return BackendHealthResult(
            ok=False,
            reachable=True,
            url=url,
            status_code=status_code,
            error="http_error",
        )
    if not isinstance(payload, dict):
        return BackendHealthResult(
            ok=False,
            reachable=True,
            url=url,
            status_code=status_code,
            error="invalid_health_payload",
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    status = str(data.get("status", "unknown"))
    ok = payload.get("success") is True and status == "ok"
    return BackendHealthResult(
        ok=ok,
        reachable=True,
        url=url,
        status_code=status_code,
        status=status,
        data=data,
        error=None if ok else "invalid_health_payload",
    )


def build_desktop_health_from_backend(
    backend_result: BackendHealthResult,
    *,
    local_data_status: object | None = None,
    model_configured: bool = False,
    mcp_enabled: bool = False,
) -> DesktopHealthStatus:
    local_ok = bool(getattr(local_data_status, "ok", True)) if local_data_status is not None else True
    local_errors = list(getattr(local_data_status, "errors", []) or []) if local_data_status is not None else []
    local_warnings = list(getattr(local_data_status, "warnings", []) or []) if local_data_status is not None else []
    errors = list(local_errors)
    if backend_result.error:
        errors.insert(0, backend_result.error)
    warnings = list(backend_result.warnings) + local_warnings
    return DesktopHealthStatus(
        ok=backend_result.ok and local_ok and not errors,
        backend_reachable=backend_result.reachable,
        model_configured=model_configured,
        mcp_enabled=mcp_enabled,
        data_dir_ready=local_ok if local_data_status is not None else False,
        errors=errors,
        warnings=warnings,
    )


def check_backend_health_from_runtime_state(
    runtime_state: DesktopRuntimeState,
    *,
    timeout: float = 1.0,
    http_get_json: Callable[..., tuple[int, dict[str, object]]] | None = None,
) -> BackendHealthResult:
    return check_backend_health(
        port=runtime_state.selected_port,
        timeout=timeout,
        http_get_json=http_get_json,
    )


def sanitize_health_error(value: object) -> str:
    text = " ".join(str(value).split())
    for pattern in SECRET_PATTERNS[:2]:
        text = pattern.sub("[REDACTED_KEY]", text)
    text = SECRET_PATTERNS[2].sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = SECRET_PATTERNS[3].sub(lambda match: f"{match.group(1)}: Bearer [REDACTED]", text)
    return text[:300]
