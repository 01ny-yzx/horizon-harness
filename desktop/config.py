"""Desktop client runtime configuration model."""

from __future__ import annotations

from dataclasses import asdict, dataclass


LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
DEFAULT_PORT_RANGE_START = 8000
DEFAULT_PORT_RANGE_END = 8999


@dataclass(frozen=True)
class DesktopRuntimeConfig:
    backend_host: str = "127.0.0.1"
    backend_port: int | None = None
    preferred_port: int = 8000
    port_range_start: int = DEFAULT_PORT_RANGE_START
    port_range_end: int = DEFAULT_PORT_RANGE_END
    auto_start_backend: bool = True
    workspace_dir: str | None = None
    data_dir: str | None = None
    log_level: str = "INFO"
    mcp_enabled: bool = True
    open_browser_on_start: bool = False


def default_desktop_config() -> DesktopRuntimeConfig:
    return DesktopRuntimeConfig()


def normalize_desktop_config(raw: dict | None) -> DesktopRuntimeConfig:
    if not isinstance(raw, dict):
        return default_desktop_config()
    range_start, range_end = _port_range(raw.get("port_range_start"), raw.get("port_range_end"))
    return DesktopRuntimeConfig(
        backend_host=_string(raw.get("backend_host"), "127.0.0.1"),
        backend_port=_optional_port(raw.get("backend_port")),
        preferred_port=_port(raw.get("preferred_port"), 8000),
        port_range_start=range_start,
        port_range_end=range_end,
        auto_start_backend=_bool(raw.get("auto_start_backend"), True),
        workspace_dir=_optional_string(raw.get("workspace_dir")),
        data_dir=_optional_string(raw.get("data_dir")),
        log_level=_log_level(raw.get("log_level")),
        mcp_enabled=_bool(raw.get("mcp_enabled"), True),
        open_browser_on_start=_bool(raw.get("open_browser_on_start"), False),
    )


def desktop_config_to_dict(config: DesktopRuntimeConfig) -> dict[str, object]:
    return asdict(config)


def _string(value: object, default: str) -> str:
    return value if isinstance(value, str) and value.strip() else default


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_port(value: object) -> int | None:
    if value is None:
        return None
    return _port(value, 8000)


def _port(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _port_range(start_value: object, end_value: object) -> tuple[int, int]:
    start = _port(start_value, DEFAULT_PORT_RANGE_START)
    end = _port(end_value, DEFAULT_PORT_RANGE_END)
    if start > end:
        return DEFAULT_PORT_RANGE_START, DEFAULT_PORT_RANGE_END
    return start, end


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return default


def _log_level(value: object) -> str:
    level = value.upper() if isinstance(value, str) else "INFO"
    return level if level in LOG_LEVELS else "INFO"
