"""Health wrappers for the Cloud MCP Gateway foundation."""

from __future__ import annotations

from cloud_mcp.config import CloudMCPGatewayConfig
from cloud_mcp.gateway import build_cloud_mcp_gateway_health_payload, build_cloud_mcp_gateway_status
from cloud_mcp.types import cloud_mcp_gateway_status_to_dict


def cloud_mcp_gateway_health_to_dict(config: CloudMCPGatewayConfig | None = None) -> dict[str, object]:
    return build_cloud_mcp_gateway_health_payload(config)


def cloud_mcp_gateway_status_to_health(config: CloudMCPGatewayConfig | None = None) -> dict[str, object]:
    status = build_cloud_mcp_gateway_status(config)
    return {
        "status": "ok",
        "service": "cloud-mcp-gateway",
        "gateway": cloud_mcp_gateway_status_to_dict(status),
    }

