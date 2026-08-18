"""JSON-friendly Cloud MCP Gateway dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CloudMCPGatewayRequest:
    service_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None


@dataclass(frozen=True)
class CloudMCPGatewayResponse:
    success: bool
    data: dict[str, Any] | None = None
    error: dict[str, object] | None = None
    request_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPGatewayStatus:
    enabled: bool
    gateway_id: str
    gateway_name: str
    status: str
    version: str
    base_path: str
    catalog_ready: bool = False
    oauth_ready: bool = False
    token_store_ready: bool = False
    permission_model_ready: bool = False
    remote_connection_ready: bool = False
    registered_services: int = 0
    warnings: list[str] = field(default_factory=list)


def cloud_mcp_gateway_request_to_dict(request: CloudMCPGatewayRequest) -> dict[str, object]:
    from cloud_mcp.gateway import sanitize_cloud_mcp_value

    return {
        "service_id": request.service_id,
        "tool_name": request.tool_name,
        "arguments": sanitize_cloud_mcp_value(request.arguments),
        "request_id": request.request_id,
    }


def cloud_mcp_gateway_response_to_dict(response: CloudMCPGatewayResponse) -> dict[str, object]:
    from cloud_mcp.gateway import sanitize_cloud_mcp_value

    return {
        "success": bool(response.success),
        "data": sanitize_cloud_mcp_value(response.data) if response.data is not None else None,
        "error": sanitize_cloud_mcp_value(response.error) if response.error is not None else None,
        "request_id": response.request_id,
        "metadata": sanitize_cloud_mcp_value(response.metadata),
    }


def cloud_mcp_gateway_status_to_dict(status: CloudMCPGatewayStatus) -> dict[str, object]:
    from cloud_mcp.gateway import sanitize_cloud_mcp_value

    return {
        "enabled": bool(status.enabled),
        "gateway_id": sanitize_cloud_mcp_value(status.gateway_id),
        "gateway_name": sanitize_cloud_mcp_value(status.gateway_name),
        "status": sanitize_cloud_mcp_value(status.status),
        "version": sanitize_cloud_mcp_value(status.version),
        "base_path": sanitize_cloud_mcp_value(status.base_path),
        "catalog_ready": bool(status.catalog_ready),
        "oauth_ready": bool(status.oauth_ready),
        "token_store_ready": bool(status.token_store_ready),
        "permission_model_ready": bool(status.permission_model_ready),
        "remote_connection_ready": bool(status.remote_connection_ready),
        "registered_services": int(status.registered_services),
        "warnings": sanitize_cloud_mcp_value(list(status.warnings)),
    }
