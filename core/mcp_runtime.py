"""Runtime construction for optional MCP tool injection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from config.settings import settings
from core.mcp_client import BaseMCPClient
from core.mcp_config import load_mcp_server_configs_from_sources
from core.mcp_remote_client import RemoteMCPClient
from core.mcp_registry import MCPRegistry
from core.mcp_stdio_client import StdioMCPClient
from core.mcp_types import MCPServerConfig, MCPToolSpec


@dataclass
class MCPRuntimeStatus:
    enabled: bool
    config_path: str
    config_dir: str = ""
    servers_total: int = 0
    servers_enabled: int = 0
    tools_total: int = 0
    tools_enabled: int = 0
    tools_disabled: int = 0
    config_sources_total: int = 0
    config_sources_loaded: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["errors"] = [_sanitize_mapping(item) for item in self.errors]
        data["diagnostics"] = _sanitize_mapping(self.diagnostics)
        return data

    def safe_dict(self) -> dict[str, Any]:
        return self.to_dict()


def build_mcp_runtime(settings_obj: Any = settings, client: BaseMCPClient | None = None) -> tuple[MCPRegistry, MCPRuntimeStatus]:
    """Build MCP registry and status for AgentLoop runtime injection."""

    registry = MCPRegistry(client=client or StdioMCPClient())
    status = MCPRuntimeStatus(
        enabled=bool(settings_obj.mcp_enabled),
        config_path=str(getattr(settings_obj, "mcp_config_path", "")),
        config_dir=str(getattr(settings_obj, "mcp_config_dir", "")),
    )
    if not status.enabled:
        return registry, status

    load_result = load_mcp_server_configs_from_sources(status.config_path, status.config_dir)
    status.config_sources_total = len(load_result.sources)
    status.config_sources_loaded = len([source for source in load_result.sources if source.loaded])
    status.diagnostics["config_sources"] = [source.to_dict() for source in load_result.sources]
    status.diagnostics["config_errors"] = list(load_result.errors)
    if load_result.configs:
        status.errors.extend(error for error in load_result.errors if error.get("code") != "mcp_config_not_found")
    else:
        status.errors.extend(load_result.errors)
    if not load_result.configs:
        has_existing_source = any(source.exists for source in load_result.sources)
        status.errors.append({"code": "mcp_no_config_sources" if not has_existing_source else "mcp_no_valid_servers"})
        return registry, status

    configs = load_result.configs
    status.servers_total = len(configs)
    enabled_configs = [config for config in configs if config.enabled]
    status.servers_enabled = len(enabled_configs)
    if not enabled_configs:
        return registry, status

    for config in enabled_configs:
        server_client = client or _client_for_transport(config.transport)
        if server_client is None:
            status.errors.append({"code": "unsupported_transport", "server_name": config.name, "transport": config.transport})
            continue
        server_registry = MCPRegistry(client=server_client, permission_policy=registry.permission_policy)
        server_registry.load_servers([config])
        try:
            discovered = server_registry.discover_tools()
        except Exception as exc:  # noqa: BLE001 - one MCP server must not break the agent.
            status.errors.append({"code": "mcp_discovery_failed", "server_name": config.name, "error": str(exc)[:300]})
            continue
        _merge_server_registry(registry, server_registry)
        status.diagnostics[config.name] = _server_diagnostics(registry.client)
        if not discovered:
            status.errors.append({"code": "mcp_no_tools_discovered", "server_name": config.name})

    tools = list(registry.tools.values())
    status.tools_total = len(tools)
    status.tools_enabled = len([tool for tool in tools if tool.enabled])
    status.tools_disabled = status.tools_total - status.tools_enabled
    status.tool_names = [tool.qualified_name for tool in tools if tool.enabled]
    return registry, status


def _merge_server_registry(target: MCPRegistry, source: MCPRegistry) -> None:
    for qualified_name, spec in source.tools.items():
        spec = _ensure_unique_in_target(target, spec)
        target.tools[qualified_name] = spec
        target.schema_name_to_qualified_name[spec.schema_name()] = qualified_name
        target.clients_by_server[spec.server_name] = source.clients_by_server.get(spec.server_name, source.client)
    target.servers.update(source.servers)


def _client_for_transport(transport: str) -> BaseMCPClient | None:
    normalized = str(transport or "stdio").strip().lower()
    if normalized == "stdio":
        return StdioMCPClient()
    if normalized in {"http", "remote_http"}:
        return RemoteMCPClient()
    return None


def _ensure_unique_in_target(target: MCPRegistry, spec: MCPToolSpec) -> MCPToolSpec:
    existing = target.schema_name_to_qualified_name.get(spec.schema_name())
    if existing is None or existing == spec.qualified_name:
        return spec
    return target._with_unique_schema_name(spec)


def _server_diagnostics(client: BaseMCPClient) -> dict[str, Any]:
    diagnostics = getattr(client, "last_diagnostics", {})
    return _sanitize_mapping(diagnostics) if isinstance(diagnostics, dict) else {}


def _sanitize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if any(secret in lowered for secret in ("token", "api_key", "apikey", "secret", "password")):
            safe[key] = "***"
        elif isinstance(value, dict):
            safe[key] = _sanitize_mapping(value)
        elif isinstance(value, list):
            safe[key] = [_sanitize_mapping(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, str):
            safe[key] = _sanitize_text(value)
        else:
            safe[key] = value
    return safe


def _sanitize_text(text: str) -> str:
    for marker in ("token", "api_key", "apikey", "secret", "password"):
        text = text.replace(marker, "[redacted]")
        text = text.replace(marker.upper(), "[REDACTED]")
    return text[:1000]
