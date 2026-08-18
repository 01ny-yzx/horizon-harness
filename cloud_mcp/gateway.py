"""Cloud MCP Gateway foundation logic."""

from __future__ import annotations

from typing import Any

from cloud_mcp.config import CloudMCPGatewayConfig, build_cloud_mcp_gateway_config_from_settings
from cloud_mcp.types import (
    CloudMCPGatewayRequest,
    CloudMCPGatewayResponse,
    CloudMCPGatewayStatus,
    cloud_mcp_gateway_response_to_dict,
    cloud_mcp_gateway_status_to_dict,
)


CLOUD_MCP_GATEWAY_DISABLED = "cloud_mcp_gateway_disabled"
CLOUD_MCP_CATALOG_NOT_READY = "cloud_mcp_catalog_not_ready"
CLOUD_MCP_OAUTH_NOT_READY = "cloud_mcp_oauth_not_ready"
CLOUD_MCP_TOKEN_STORE_NOT_READY = "cloud_mcp_token_store_not_ready"
CLOUD_MCP_PERMISSION_MODEL_NOT_READY = "cloud_mcp_permission_model_not_ready"
CLOUD_MCP_REMOTE_CONNECTION_NOT_READY = "cloud_mcp_remote_connection_not_ready"
CLOUD_MCP_REMOTE_EXECUTION_NOT_READY = "cloud_mcp_remote_execution_not_ready"
CLOUD_MCP_INVALID_REQUEST = "cloud_mcp_invalid_request"

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "token",
    "password",
    "secret",
    "credential",
    "authorization",
    "bearer",
}
_SENSITIVE_TEXT = ("api_key", "apikey", "api-key", "token", "password", "secret", "credential", "authorization", "bearer", "sk-", "tvly-")


def _is_sensitive_text(value: str) -> bool:
    lower = value.lower()
    return any(marker in lower for marker in _SENSITIVE_TEXT)


def sanitize_cloud_mcp_value(value: object, *, max_length: int = 1000) -> object:
    try:
        if isinstance(value, dict):
            sanitized: dict[str, object] = {}
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                if key.lower() in _SENSITIVE_KEYS or _is_sensitive_text(key):
                    sanitized["redacted"] = _REDACTED
                else:
                    sanitized[key] = sanitize_cloud_mcp_value(raw_value, max_length=max_length)
            return sanitized
        if isinstance(value, (list, tuple, set)):
            return [sanitize_cloud_mcp_value(item, max_length=max_length) for item in value]
        if isinstance(value, str):
            if _is_sensitive_text(value):
                return _REDACTED
            if len(value) > max_length:
                return f"{value[:max_length]}...[truncated]"
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        text = str(value)
        if _is_sensitive_text(text):
            return _REDACTED
        if len(text) > max_length:
            return f"{text[:max_length]}...[truncated]"
        return text
    except Exception:  # noqa: BLE001
        return _REDACTED


def build_cloud_mcp_gateway_status(
    config: CloudMCPGatewayConfig | None = None,
) -> CloudMCPGatewayStatus:
    config = config or build_cloud_mcp_gateway_config_from_settings()
    warnings: list[str] = []
    if not config.catalog_ready:
        warnings.append(CLOUD_MCP_CATALOG_NOT_READY)
    if not config.oauth_ready:
        warnings.append(CLOUD_MCP_OAUTH_NOT_READY)
    if not config.token_store_ready:
        warnings.append(CLOUD_MCP_TOKEN_STORE_NOT_READY)
    if not config.permission_model_ready:
        warnings.append(CLOUD_MCP_PERMISSION_MODEL_NOT_READY)
    if not config.remote_connection_ready:
        warnings.append(CLOUD_MCP_REMOTE_CONNECTION_NOT_READY)
    elif not _remote_execution_enabled():
        warnings.append(CLOUD_MCP_REMOTE_EXECUTION_NOT_READY)
    return CloudMCPGatewayStatus(
        enabled=config.enabled,
        gateway_id=config.gateway_id,
        gateway_name=config.gateway_name,
        status="ready" if config.enabled else "disabled",
        version=config.version,
        base_path=config.base_path,
        catalog_ready=bool(config.catalog_ready),
        oauth_ready=bool(config.oauth_ready),
        token_store_ready=bool(config.token_store_ready),
        permission_model_ready=bool(config.permission_model_ready),
        remote_connection_ready=bool(config.remote_connection_ready),
        registered_services=_catalog_service_count() if config.catalog_ready else 0,
        warnings=warnings,
    )


def _catalog_service_count() -> int:
    try:
        from cloud_mcp.catalog import default_cloud_mcp_catalog_services

        return len(default_cloud_mcp_catalog_services())
    except Exception:  # noqa: BLE001
        return 0


def build_cloud_mcp_gateway_health_payload(
    config: CloudMCPGatewayConfig | None = None,
) -> dict[str, object]:
    config = config or build_cloud_mcp_gateway_config_from_settings()
    status = build_cloud_mcp_gateway_status(config)
    return {
        "status": "ok",
        "service": "cloud-mcp-gateway",
        "enabled": bool(config.enabled),
        "gateway": cloud_mcp_gateway_status_to_dict(status),
    }


def parse_cloud_mcp_gateway_request(payload: object) -> CloudMCPGatewayRequest | None:
    if not isinstance(payload, dict):
        return None
    service_id = payload.get("service_id")
    tool_name = payload.get("tool_name")
    arguments = payload.get("arguments", {})
    request_id = payload.get("request_id")
    if not isinstance(service_id, str) or not service_id.strip():
        return None
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    if not isinstance(arguments, dict):
        return None
    if request_id is not None and not isinstance(request_id, str):
        request_id = str(request_id)
    return CloudMCPGatewayRequest(
        service_id=service_id.strip(),
        tool_name=tool_name.strip(),
        arguments=arguments,
        request_id=request_id,
    )


def _error_response(code: str, message: str, request_id: str | None = None) -> CloudMCPGatewayResponse:
    return CloudMCPGatewayResponse(
        success=False,
        error={"code": code, "message": message},
        request_id=request_id,
        metadata={"gateway": "cloud-mcp-gateway", "placeholder": True},
    )


def invoke_cloud_mcp_gateway(
    payload: object,
    *,
    config: CloudMCPGatewayConfig | None = None,
) -> CloudMCPGatewayResponse:
    request = parse_cloud_mcp_gateway_request(payload)
    if request is None:
        return _error_response(CLOUD_MCP_INVALID_REQUEST, "Invalid Cloud MCP Gateway request.")
    config = config or build_cloud_mcp_gateway_config_from_settings()
    if not config.enabled:
        return _error_response(CLOUD_MCP_GATEWAY_DISABLED, "Cloud MCP Gateway is disabled.", request.request_id)
    if not config.catalog_ready:
        return _error_response(CLOUD_MCP_CATALOG_NOT_READY, "Cloud MCP catalog is not ready.", request.request_id)
    if not config.oauth_ready:
        return _error_response(CLOUD_MCP_OAUTH_NOT_READY, "Cloud MCP OAuth foundation is not ready.", request.request_id)
    if not config.token_store_ready:
        return _error_response(CLOUD_MCP_TOKEN_STORE_NOT_READY, "Cloud MCP token store foundation is not ready.", request.request_id)
    if not config.permission_model_ready:
        return _error_response(CLOUD_MCP_PERMISSION_MODEL_NOT_READY, "Cloud MCP permission model is not ready.", request.request_id)
    if not config.remote_connection_ready:
        return _error_response(CLOUD_MCP_REMOTE_CONNECTION_NOT_READY, "Cloud MCP remote connection is not ready.", request.request_id)
    from cloud_mcp.remote_connection import (
        build_remote_call_request_from_gateway_request,
        cloud_mcp_remote_call_result_to_dict,
        execute_cloud_mcp_remote_connection,
    )

    remote_request = build_remote_call_request_from_gateway_request(request, payload=payload)
    remote_result = execute_cloud_mcp_remote_connection(remote_request)
    return CloudMCPGatewayResponse(
        success=remote_result.success,
        data=cloud_mcp_remote_call_result_to_dict(remote_result),
        error=remote_result.error,
        request_id=remote_result.request_id,
        metadata={"remote_result": cloud_mcp_remote_call_result_to_dict(remote_result), "gateway": "cloud-mcp-gateway"},
    )


def _remote_execution_enabled() -> bool:
    try:
        from config.settings import settings

        return bool(getattr(settings, "cloud_mcp_remote_execution_enabled", False))
    except Exception:  # noqa: BLE001
        return False


def invoke_cloud_mcp_gateway_to_dict(payload: object, *, config: CloudMCPGatewayConfig | None = None) -> dict[str, object]:
    return cloud_mcp_gateway_response_to_dict(invoke_cloud_mcp_gateway(payload, config=config))
