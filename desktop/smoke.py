"""Desktop Foundation smoke orchestration."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from desktop.config import DesktopRuntimeConfig
from desktop.log_viewer import (
    desktop_log_list_result_to_dict,
    desktop_log_read_result_to_dict,
    list_desktop_log_files,
    read_desktop_log_tail,
)
from desktop.runtime_config import (
    desktop_runtime_config_bridge_to_dict,
    prepare_desktop_runtime_config,
)


SMOKE_LOG_NAME = "desktop-smoke.log"
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|password|secret|credential)\b\s*([:=])\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(authorization)\b\s*:\s*bearer\s+([^\s,;]+)"),
]


@dataclass(frozen=True)
class DesktopFoundationSmokeResult:
    ok: bool
    config: dict[str, object]
    paths: dict[str, object]
    runtime_config: dict[str, object] | None = None
    local_data: dict[str, object] | None = None
    port_allocation: dict[str, object] | None = None
    process_spec: dict[str, object] | None = None
    runtime_state: dict[str, object] | None = None
    health: dict[str, object] | None = None
    logs: dict[str, object] | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def run_desktop_foundation_smoke(
    raw_config: dict | DesktopRuntimeConfig | None = None,
    *,
    base_dir: str | Path | None = None,
    app_name: str = "agent",
    ensure_dirs: bool = False,
    create_sample_log: bool = False,
    check_backend: bool = False,
    port_checker: Any | None = None,
    http_get_json: Any | None = None,
) -> DesktopFoundationSmokeResult:
    if base_dir is None and (ensure_dirs or create_sample_log):
        return _error_result("missing_base_dir_for_write_smoke")

    bridge = prepare_desktop_runtime_config(
        raw_config,
        base_dir=base_dir,
        app_name=app_name,
        ensure_dirs=ensure_dirs,
        validate_dirs=True,
        allocate_dynamic_port=True,
        port_checker=port_checker,
        check_backend=check_backend,
        http_get_json=http_get_json,
    )
    bridge_dict = desktop_runtime_config_bridge_to_dict(bridge)
    logs_data: dict[str, object] | None = None
    log_errors: list[str] = []
    log_warnings: list[str] = []
    secrets_redacted = True

    if create_sample_log:
        try:
            bridge.paths.logs_dir.mkdir(parents=True, exist_ok=True)
            (bridge.paths.logs_dir / SMOKE_LOG_NAME).write_text(
                "desktop smoke log ready\nsecret sk-test-secret api_key=sample\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log_errors.append(sanitize_smoke_error(exc))

    list_result = list_desktop_log_files(bridge.paths.logs_dir)
    read_result = read_desktop_log_tail(bridge.paths.logs_dir, SMOKE_LOG_NAME, tail_lines=50)
    if create_sample_log:
        log_errors.extend(list_result.errors)
        log_errors.extend(read_result.errors)
        log_warnings.extend(list_result.warnings)
        log_warnings.extend(read_result.warnings)
        read_text = read_result.content + "\n".join(read_result.lines)
        secrets_redacted = "sk-test-secret" not in read_text and "api_key=sample" not in read_text
        logs_data = {
            "list": desktop_log_list_result_to_dict(list_result),
            "tail": desktop_log_read_result_to_dict(read_result),
        }
    else:
        logs_data = {
            "list": desktop_log_list_result_to_dict(list_result),
            "tail": None,
        }
        if list_result.errors:
            log_warnings.extend(list_result.errors)

    errors = [sanitize_smoke_error(error) for error in list(bridge.errors) + log_errors]
    warnings = [sanitize_smoke_error(warning) for warning in list(bridge.warnings) + log_warnings]
    checks = {
        "config_ready": bool(bridge_dict.get("config")),
        "paths_ready": bool(bridge_dict.get("paths")),
        "local_data_ready": bool(bridge.local_data_status.ok) if bridge.local_data_status is not None else False,
        "port_selected": bridge.process_spec.port is not None,
        "process_spec_ready": bool(bridge.process_spec.command),
        "runtime_state_ready": bridge.runtime_state.status in {"configured", "stopped"},
        "health_ready": bridge.health_status.ok,
        "logs_ready": read_result.ok if create_sample_log else True,
        "secrets_redacted": secrets_redacted,
    }
    ok = (
        not errors
        and bridge.ok
        and (checks["local_data_ready"] if ensure_dirs else True)
        and (checks["health_ready"] if check_backend else True)
        and (checks["logs_ready"] and checks["secrets_redacted"] if create_sample_log else True)
    )
    return DesktopFoundationSmokeResult(
        ok=ok,
        config=dict(bridge_dict["config"]),
        paths=dict(bridge_dict["paths"]),
        runtime_config=bridge_dict,
        local_data=bridge_dict.get("local_data_status") if isinstance(bridge_dict.get("local_data_status"), dict) else None,
        port_allocation=bridge.port_allocation,
        process_spec=bridge_dict.get("process_spec") if isinstance(bridge_dict.get("process_spec"), dict) else None,
        runtime_state=bridge_dict.get("runtime_state") if isinstance(bridge_dict.get("runtime_state"), dict) else None,
        health=bridge_dict.get("health_status") if isinstance(bridge_dict.get("health_status"), dict) else None,
        logs=logs_data,
        checks=checks,
        errors=errors,
        warnings=warnings,
        metadata={
            "ensure_dirs": ensure_dirs,
            "create_sample_log": create_sample_log,
            "check_backend": check_backend,
        },
    )


def desktop_foundation_smoke_result_to_dict(
    result: DesktopFoundationSmokeResult,
) -> dict[str, object]:
    return asdict(result)


def sanitize_smoke_error(value: object) -> str:
    text = " ".join(str(value).split())
    for pattern in SECRET_PATTERNS[:2]:
        text = pattern.sub("[REDACTED_KEY]", text)
    text = SECRET_PATTERNS[2].sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = SECRET_PATTERNS[3].sub(lambda match: f"{match.group(1)}: Bearer [REDACTED]", text)
    return text[:300]


def _error_result(error: str) -> DesktopFoundationSmokeResult:
    safe_error = sanitize_smoke_error(error)
    return DesktopFoundationSmokeResult(
        ok=False,
        config={},
        paths={},
        errors=[safe_error],
        checks={
            "config_ready": False,
            "paths_ready": False,
            "local_data_ready": False,
            "port_selected": False,
            "process_spec_ready": False,
            "runtime_state_ready": False,
            "health_ready": False,
            "logs_ready": False,
            "secrets_redacted": True,
        },
    )
