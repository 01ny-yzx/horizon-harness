"""Desktop runtime configuration bridge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desktop.config import DesktopRuntimeConfig, desktop_config_to_dict, normalize_desktop_config
from desktop.health import (
    DesktopHealthStatus,
    build_desktop_health_from_backend,
    build_desktop_health_from_local_data,
    build_static_desktop_health,
    check_backend_health,
    desktop_health_to_dict,
)
from desktop.local_data import (
    LocalDataDirectoryStatus,
    build_desktop_paths_from_config,
    ensure_local_data_dirs,
    local_data_directory_status_to_dict,
    validate_local_data_dirs,
)
from desktop.paths import DesktopPaths, desktop_paths_to_dict
from desktop.process_manager import BackendProcessSpec, build_backend_process_spec
from desktop.runtime import DesktopRuntimeState, build_runtime_state_from_desktop_paths, desktop_runtime_state_to_dict


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|password|secret|credential)\b\s*([:=])\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(authorization)\b\s*:\s*bearer\s+([^\s,;]+)"),
]


@dataclass(frozen=True)
class DesktopRuntimeConfigBridgeResult:
    ok: bool
    config: DesktopRuntimeConfig
    paths: DesktopPaths
    local_data_status: LocalDataDirectoryStatus | None
    port_allocation: dict[str, object] | None
    process_spec: BackendProcessSpec
    runtime_state: DesktopRuntimeState
    health_status: DesktopHealthStatus
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def prepare_desktop_runtime_config(
    raw_config: dict | DesktopRuntimeConfig | None = None,
    *,
    base_dir: str | Path | None = None,
    app_name: str = "agent",
    ensure_dirs: bool = False,
    validate_dirs: bool = True,
    allocate_dynamic_port: bool = True,
    port_checker: Any | None = None,
    check_backend: bool = False,
    http_get_json: Any | None = None,
) -> DesktopRuntimeConfigBridgeResult:
    config = raw_config if isinstance(raw_config, DesktopRuntimeConfig) else normalize_desktop_config(raw_config)
    paths = build_desktop_paths_from_config(config, base_dir=base_dir, app_name=app_name)
    local_data_status: LocalDataDirectoryStatus | None = None
    if ensure_dirs:
        local_data_status = ensure_local_data_dirs(paths)
    elif validate_dirs:
        local_data_status = validate_local_data_dirs(paths)

    process_spec = build_backend_process_spec(
        host=config.backend_host,
        port=config.backend_port,
        preferred_port=config.preferred_port,
        port_range=(config.port_range_start, config.port_range_end),
        allocate_dynamic_port=allocate_dynamic_port,
        port_checker=port_checker,
        cwd=None,
    )
    errors, warnings = _collect_runtime_bridge_errors(local_data_status, process_spec.port_allocation)
    backend_url = f"http://{config.backend_host}:{process_spec.port}" if process_spec.port is not None else None
    runtime_state = build_runtime_state_from_desktop_paths(
        paths,
        backend_running=False,
        backend_pid=None,
        backend_url=backend_url,
        selected_port=process_spec.port,
        status="configured",
        errors=errors,
    )
    if check_backend:
        backend_result = check_backend_health(
            host=config.backend_host,
            port=process_spec.port,
            http_get_json=http_get_json,
        )
        health_status = build_desktop_health_from_backend(
            backend_result,
            local_data_status=local_data_status,
            mcp_enabled=config.mcp_enabled,
        )
        if not backend_result.ok:
            errors.append(f"backend_health_failed:{backend_result.error or backend_result.status}")
        warnings.extend(backend_result.warnings)
    elif local_data_status is not None:
        health_status = build_desktop_health_from_local_data(
            local_data_status,
            backend_reachable=False,
            mcp_enabled=config.mcp_enabled,
        )
    else:
        health_status = build_static_desktop_health(mcp_enabled=config.mcp_enabled)

    errors.extend(error for error in health_status.errors if error not in errors)
    warnings.extend(warning for warning in health_status.warnings if warning not in warnings)
    safe_errors = [sanitize_runtime_config_error(error) for error in errors]
    safe_warnings = [sanitize_runtime_config_error(warning) for warning in warnings]
    return DesktopRuntimeConfigBridgeResult(
        ok=not safe_errors and health_status.ok,
        config=config,
        paths=paths,
        local_data_status=local_data_status,
        port_allocation=process_spec.port_allocation,
        process_spec=process_spec,
        runtime_state=runtime_state,
        health_status=health_status,
        errors=safe_errors,
        warnings=safe_warnings,
        metadata={
            "ensure_dirs": ensure_dirs,
            "validate_dirs": validate_dirs,
            "allocate_dynamic_port": allocate_dynamic_port,
            "check_backend": check_backend,
        },
    )


def desktop_runtime_config_bridge_to_dict(
    result: DesktopRuntimeConfigBridgeResult,
) -> dict[str, object]:
    return {
        "ok": result.ok,
        "config": desktop_config_to_dict(result.config),
        "paths": desktop_paths_to_dict(result.paths),
        "local_data_status": (
            local_data_directory_status_to_dict(result.local_data_status)
            if result.local_data_status is not None
            else None
        ),
        "port_allocation": result.port_allocation,
        "process_spec": safe_process_spec_to_dict(result.process_spec),
        "runtime_state": desktop_runtime_state_to_dict(result.runtime_state),
        "health_status": desktop_health_to_dict(result.health_status),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
        "metadata": dict(result.metadata),
    }


def safe_process_spec_to_dict(spec: BackendProcessSpec) -> dict[str, object]:
    env = spec.env or {}
    safe_env = {key: env[key] for key in ("API_HOST", "API_PORT") if key in env}
    return {
        "host": spec.host,
        "port": spec.port,
        "command": list(spec.command),
        "cwd": spec.cwd,
        "env": safe_env,
        "startup_timeout_seconds": spec.startup_timeout_seconds,
        "shutdown_timeout_seconds": spec.shutdown_timeout_seconds,
        "port_allocation": spec.port_allocation,
    }


def sanitize_runtime_config_error(value: object) -> str:
    text = " ".join(str(value).split())
    for pattern in SECRET_PATTERNS[:2]:
        text = pattern.sub("[REDACTED_KEY]", text)
    text = SECRET_PATTERNS[2].sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = SECRET_PATTERNS[3].sub(lambda match: f"{match.group(1)}: Bearer [REDACTED]", text)
    return text[:300]


def _collect_runtime_bridge_errors(
    local_data_status: LocalDataDirectoryStatus | None,
    port_allocation: dict[str, object] | None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if local_data_status is not None:
        if not local_data_status.ok:
            errors.append("local_data_not_ready")
        errors.extend(local_data_status.errors)
        warnings.extend(local_data_status.warnings)
    if port_allocation and port_allocation.get("ok") is False:
        reason = str(port_allocation.get("reason", "unknown"))
        errors.append(f"port_allocation_failed:{reason}")
    if port_allocation:
        warnings.extend(str(warning) for warning in port_allocation.get("warnings", []) if isinstance(warning, str))
    return errors, warnings
