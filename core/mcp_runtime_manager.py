"""Thread-safe MCP runtime snapshot and manual reload manager."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any, Callable

from config.settings import settings
from core.mcp_client import BaseMCPClient
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus, build_mcp_runtime


BuildRuntime = Callable[[Any, BaseMCPClient | None], tuple[MCPRegistry, MCPRuntimeStatus]]
ClientFactory = Callable[[], BaseMCPClient | None]


class MCPRuntimeManager:
    """Manage the current MCP registry snapshot and explicit runtime reloads."""

    def __init__(
        self,
        settings_obj: Any = settings,
        *,
        client_factory: ClientFactory | None = None,
        build_runtime: BuildRuntime = build_mcp_runtime,
    ) -> None:
        self.settings_obj = settings_obj
        self.client_factory = client_factory
        self.build_runtime = build_runtime
        self._lock = RLock()
        self._registry = MCPRegistry()
        self._status = self._empty_status()
        self._loaded = False
        self.reload_count = 0
        self.last_reload_at: str | None = None
        self.last_reload_success = False
        self.last_reload_error = ""
        self.previous_tools_total = 0
        self.current_tools_total = 0
        self.added_tools: list[str] = []
        self.removed_tools: list[str] = []
        self.unchanged_tools: list[str] = []

    def reload(self) -> dict[str, Any]:
        """Re-read MCP config, rediscover tools, and atomically swap the snapshot."""

        with self._lock:
            previous_status = self._status
            previous_tool_names = set(previous_status.tool_names)
            client = self.client_factory() if self.client_factory else None
            try:
                registry, status = self.build_runtime(self.settings_obj, client)
                current_tool_names = set(status.tool_names)
                self.added_tools = sorted(current_tool_names - previous_tool_names)
                self.removed_tools = sorted(previous_tool_names - current_tool_names)
                self.unchanged_tools = sorted(previous_tool_names & current_tool_names)
                self.previous_tools_total = previous_status.tools_total
                self.current_tools_total = status.tools_total
                self.reload_count += 1
                self.last_reload_at = _utc_now()
                self.last_reload_success = True
                self.last_reload_error = ""
                self._registry = registry
                self._status = status
                self._loaded = True
                return self._reload_result(previous_status, status, success=True)
            except Exception as exc:  # noqa: BLE001 - runtime reload must remain API-safe.
                self.reload_count += 1
                self.last_reload_at = _utc_now()
                self.last_reload_success = False
                self.last_reload_error = _sanitize_text(str(exc))
                self.added_tools = []
                self.removed_tools = []
                self.unchanged_tools = sorted(previous_tool_names)
                return self._reload_result(previous_status, self._status, success=False)

    def status(self) -> dict[str, Any]:
        """Return the current safe runtime state without forcing discovery."""

        with self._lock:
            return self._status_payload()

    def snapshot(self) -> tuple[MCPRegistry, MCPRuntimeStatus]:
        """Return a consistent registry/status snapshot, lazily loading once."""

        with self._lock:
            if not self._loaded:
                self.reload()
            return self._registry, self._status

    def get_registry(self) -> MCPRegistry:
        return self.snapshot()[0]

    def get_status(self) -> MCPRuntimeStatus:
        return self.snapshot()[1]

    def reset_for_tests(self) -> None:
        with self._lock:
            self._registry = MCPRegistry()
            self._status = self._empty_status()
            self._loaded = False
            self.reload_count = 0
            self.last_reload_at = None
            self.last_reload_success = False
            self.last_reload_error = ""
            self.previous_tools_total = 0
            self.current_tools_total = 0
            self.added_tools = []
            self.removed_tools = []
            self.unchanged_tools = []

    def _empty_status(self) -> MCPRuntimeStatus:
        return MCPRuntimeStatus(
            enabled=bool(getattr(self.settings_obj, "mcp_enabled", False)),
            config_path=str(getattr(self.settings_obj, "mcp_config_path", "")),
            config_dir=str(getattr(self.settings_obj, "mcp_config_dir", "")),
        )

    def _status_payload(self) -> dict[str, Any]:
        safe_status = self._status.safe_dict()
        runtime = {
            **safe_status,
            "reload_count": self.reload_count,
            "last_reload_at": self.last_reload_at,
            "last_reload_success": self.last_reload_success,
            "last_reload_error": self.last_reload_error,
        }
        return {
            "runtime": runtime,
            "config": {
                "config_path": safe_status.get("config_path", ""),
                "config_dir": safe_status.get("config_dir", ""),
            },
            "errors": safe_status.get("errors", []),
            "diagnostics": safe_status.get("diagnostics", {}),
        }

    def _reload_result(self, previous: MCPRuntimeStatus, current: MCPRuntimeStatus, *, success: bool) -> dict[str, Any]:
        result = {
            "success": success,
            "reload_count": self.reload_count,
            "last_reload_at": self.last_reload_at,
            "last_reload_success": self.last_reload_success,
            "last_reload_error": self.last_reload_error,
            "previous": _summary(previous),
            "current": _summary(current),
            "diff": {
                "added_tools": list(self.added_tools),
                "removed_tools": list(self.removed_tools),
                "unchanged_tools": list(self.unchanged_tools),
            },
            "status": current.safe_dict(),
        }
        return _sanitize_mapping(result)


_GLOBAL_MANAGER: MCPRuntimeManager | None = None


def get_mcp_runtime_manager() -> MCPRuntimeManager:
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        _GLOBAL_MANAGER = MCPRuntimeManager()
    return _GLOBAL_MANAGER


def _summary(status: MCPRuntimeStatus) -> dict[str, int]:
    return {
        "tools_total": status.tools_total,
        "tools_enabled": status.tools_enabled,
        "tools_disabled": status.tools_disabled,
        "servers_total": status.servers_total,
        "servers_enabled": status.servers_enabled,
    }


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sanitize_text(text: str) -> str:
    for marker in ("token", "api_key", "apikey", "secret", "password"):
        text = text.replace(marker, "[redacted]")
        text = text.replace(marker.upper(), "[REDACTED]")
    return text[:1000]


def _sanitize_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "api_key", "apikey", "secret", "password", "env_values")):
                safe[key] = "[redacted]"
            else:
                safe[key] = _sanitize_mapping(item)
        return safe
    if isinstance(value, list):
        return [_sanitize_mapping(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value
