"""Cloud MCP Gateway configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_CLOUD_MCP_BASE_PATH = "/cloud-mcp"


@dataclass(frozen=True)
class CloudMCPGatewayConfig:
    enabled: bool = False
    gateway_id: str = "default-cloud-mcp-gateway"
    gateway_name: str = "Cloud MCP Gateway"
    version: str = "0.1.0"
    base_path: str = DEFAULT_CLOUD_MCP_BASE_PATH
    invoke_path: str = "/cloud-mcp/invoke"
    catalog_ready: bool = False
    oauth_ready: bool = False
    token_store_ready: bool = False
    permission_model_ready: bool = False
    remote_connection_ready: bool = False


def normalize_cloud_mcp_base_path(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_CLOUD_MCP_BASE_PATH
    path = value.strip()
    if not path.startswith("/") or path == "/" or " " in path or "?" in path or "#" in path:
        return DEFAULT_CLOUD_MCP_BASE_PATH
    return path.rstrip("/") or DEFAULT_CLOUD_MCP_BASE_PATH


def default_cloud_mcp_gateway_config() -> CloudMCPGatewayConfig:
    return CloudMCPGatewayConfig()


def build_cloud_mcp_gateway_config_from_settings() -> CloudMCPGatewayConfig:
    from config.settings import settings

    base_path = normalize_cloud_mcp_base_path(getattr(settings, "cloud_mcp_gateway_base_path", DEFAULT_CLOUD_MCP_BASE_PATH))
    return CloudMCPGatewayConfig(
        enabled=bool(getattr(settings, "cloud_mcp_gateway_enabled", False)),
        gateway_id=str(getattr(settings, "cloud_mcp_gateway_id", "default-cloud-mcp-gateway") or "default-cloud-mcp-gateway"),
        gateway_name=str(getattr(settings, "cloud_mcp_gateway_name", "Cloud MCP Gateway") or "Cloud MCP Gateway"),
        base_path=base_path,
        invoke_path=f"{base_path}/invoke",
        catalog_ready=bool(getattr(settings, "cloud_mcp_catalog_enabled", True)),
        oauth_ready=bool(getattr(settings, "cloud_mcp_oauth_foundation_enabled", True)),
        token_store_ready=bool(getattr(settings, "cloud_mcp_token_store_enabled", True)),
        permission_model_ready=bool(getattr(settings, "cloud_mcp_permission_model_enabled", True)),
        remote_connection_ready=bool(getattr(settings, "cloud_mcp_remote_connection_enabled", True)),
    )


def cloud_mcp_gateway_config_to_dict(config: CloudMCPGatewayConfig) -> dict[str, object]:
    from cloud_mcp.gateway import sanitize_cloud_mcp_value

    return {
        "enabled": bool(config.enabled),
        "gateway_id": sanitize_cloud_mcp_value(config.gateway_id),
        "gateway_name": sanitize_cloud_mcp_value(config.gateway_name),
        "version": sanitize_cloud_mcp_value(config.version),
        "base_path": sanitize_cloud_mcp_value(normalize_cloud_mcp_base_path(config.base_path)),
        "invoke_path": sanitize_cloud_mcp_value(config.invoke_path),
        "catalog_ready": bool(config.catalog_ready),
        "oauth_ready": bool(config.oauth_ready),
        "token_store_ready": bool(config.token_store_ready),
        "permission_model_ready": bool(config.permission_model_ready),
        "remote_connection_ready": bool(config.remote_connection_ready),
    }
