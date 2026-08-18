"""Cloud MCP remote connection foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cloud_mcp.catalog import get_cloud_mcp_catalog_service, list_cloud_mcp_catalog_services
from cloud_mcp.gateway import sanitize_cloud_mcp_value
from cloud_mcp.permissions import (
    ACTION_CONNECT,
    ACTION_INVOKE,
    ACTION_READ,
    ACTION_WRITE,
    CLOUD_MCP_ACCOUNT_CONNECTION_REQUIRED,
    CLOUD_MCP_PERMISSION_ALLOWED,
    CLOUD_MCP_USER_CONFIRMATION_REQUIRED,
    CloudMCPPermissionEvaluationRequest,
    cloud_mcp_permission_decision_to_dict,
    evaluate_cloud_mcp_permission,
)
from cloud_mcp.types import CloudMCPGatewayRequest


CLOUD_MCP_REMOTE_CONNECTION_NOT_READY = "cloud_mcp_remote_connection_not_ready"
CLOUD_MCP_REMOTE_EXECUTION_NOT_READY = "cloud_mcp_remote_execution_not_ready"
CLOUD_MCP_REMOTE_REQUEST_INVALID = "cloud_mcp_remote_request_invalid"
CLOUD_MCP_REMOTE_SERVICE_NOT_FOUND = "cloud_mcp_remote_service_not_found"
CLOUD_MCP_REMOTE_PERMISSION_DENIED = "cloud_mcp_remote_permission_denied"
CLOUD_MCP_REMOTE_ACCOUNT_CONNECTION_REQUIRED = "cloud_mcp_account_connection_required"
CLOUD_MCP_REMOTE_USER_CONFIRMATION_REQUIRED = "cloud_mcp_user_confirmation_required"
CLOUD_MCP_REMOTE_SERVICE_DISABLED = "cloud_mcp_remote_service_disabled"


@dataclass(frozen=True)
class CloudMCPRemoteConnectionStatus:
    enabled: bool
    ready: bool
    mode: str = "foundation"
    remote_execution_enabled: bool = False
    available_services: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPRemoteCallRequest:
    service_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    subject_id: str = "default"
    account_connected: bool = False
    user_confirmed: bool = False
    requested_permissions: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPRemoteCallResult:
    success: bool
    service_id: str
    tool_name: str
    data: dict[str, Any] | None = None
    error: dict[str, object] | None = None
    request_id: str | None = None
    permission_decision: dict[str, object] | None = None
    remote_checked: bool = False
    executed: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


def cloud_mcp_remote_connection_status_to_dict(
    status: CloudMCPRemoteConnectionStatus,
) -> dict[str, object]:
    return {
        "enabled": bool(status.enabled),
        "ready": bool(status.ready),
        "mode": sanitize_cloud_mcp_value(status.mode),
        "remote_execution_enabled": bool(status.remote_execution_enabled),
        "available_services": sanitize_cloud_mcp_value(list(status.available_services)),
        "warnings": sanitize_cloud_mcp_value(list(status.warnings)),
        "metadata": sanitize_cloud_mcp_value(dict(status.metadata)),
    }


def cloud_mcp_remote_call_request_to_dict(
    request: CloudMCPRemoteCallRequest,
) -> dict[str, object]:
    return {
        "service_id": sanitize_cloud_mcp_value(request.service_id),
        "tool_name": sanitize_cloud_mcp_value(request.tool_name),
        "arguments": sanitize_cloud_mcp_value(dict(request.arguments)),
        "request_id": sanitize_cloud_mcp_value(request.request_id) if request.request_id is not None else None,
        "subject_id": sanitize_cloud_mcp_value(request.subject_id),
        "account_connected": bool(request.account_connected),
        "user_confirmed": bool(request.user_confirmed),
        "requested_permissions": sanitize_cloud_mcp_value(list(request.requested_permissions)),
        "metadata": sanitize_cloud_mcp_value(dict(request.metadata)),
    }


def cloud_mcp_remote_call_result_to_dict(
    result: CloudMCPRemoteCallResult,
) -> dict[str, object]:
    return {
        "success": bool(result.success),
        "service_id": sanitize_cloud_mcp_value(result.service_id),
        "tool_name": sanitize_cloud_mcp_value(result.tool_name),
        "data": sanitize_cloud_mcp_value(result.data) if result.data is not None else None,
        "error": sanitize_cloud_mcp_value(result.error) if result.error is not None else None,
        "request_id": sanitize_cloud_mcp_value(result.request_id) if result.request_id is not None else None,
        "permission_decision": sanitize_cloud_mcp_value(result.permission_decision) if result.permission_decision is not None else None,
        "remote_checked": bool(result.remote_checked),
        "executed": bool(result.executed),
        "metadata": sanitize_cloud_mcp_value(dict(result.metadata)),
    }


def build_cloud_mcp_remote_connection_status(
    *,
    enabled: bool | None = None,
) -> CloudMCPRemoteConnectionStatus:
    if enabled is None:
        from config.settings import settings

        enabled = bool(getattr(settings, "cloud_mcp_remote_connection_enabled", True))
        mode = str(getattr(settings, "cloud_mcp_remote_connection_mode", "foundation") or "foundation")
        remote_execution_enabled = bool(getattr(settings, "cloud_mcp_remote_execution_enabled", False))
    else:
        mode = "foundation"
        remote_execution_enabled = False
    services = [service.service_id for service in list_cloud_mcp_catalog_services()]
    return CloudMCPRemoteConnectionStatus(
        enabled=bool(enabled),
        ready=bool(enabled),
        mode=mode,
        remote_execution_enabled=remote_execution_enabled and bool(enabled),
        available_services=services,
        warnings=[CLOUD_MCP_REMOTE_EXECUTION_NOT_READY],
        metadata={"remote_connection": "foundation", "agent_runtime": "client_side"},
    )


def build_remote_call_request_from_gateway_payload(
    payload: object,
) -> CloudMCPRemoteCallRequest | None:
    if not isinstance(payload, dict):
        return None
    service_id = payload.get("service_id")
    tool_name = payload.get("tool_name")
    if not isinstance(service_id, str) or not service_id.strip():
        return None
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    arguments = payload.get("arguments", {})
    requested = payload.get("requested_permissions", [])
    if not isinstance(arguments, dict):
        arguments = {}
    if not isinstance(requested, list):
        requested = []
    inferred_action = infer_remote_action_from_tool_name(tool_name)
    inferred_permissions = infer_requested_permissions_from_action(inferred_action)
    return CloudMCPRemoteCallRequest(
        service_id=service_id.strip(),
        tool_name=tool_name.strip(),
        arguments=arguments,
        request_id=str(payload["request_id"]) if payload.get("request_id") is not None else None,
        subject_id=str(payload.get("subject_id", "default") or "default"),
        account_connected=bool(payload.get("account_connected", False)),
        user_confirmed=bool(payload.get("user_confirmed", False)),
        requested_permissions=[str(item) for item in requested if isinstance(item, str)] or inferred_permissions,
        metadata={"source": "gateway_payload", "action": inferred_action},
    )


def build_remote_call_request_from_gateway_request(
    request: CloudMCPGatewayRequest,
    *,
    payload: object | None = None,
) -> CloudMCPRemoteCallRequest:
    from_payload = build_remote_call_request_from_gateway_payload(payload) if payload is not None else None
    if from_payload is not None:
        return from_payload
    action = infer_remote_action_from_tool_name(request.tool_name)
    return CloudMCPRemoteCallRequest(
        service_id=request.service_id,
        tool_name=request.tool_name,
        arguments=request.arguments,
        request_id=request.request_id,
        requested_permissions=infer_requested_permissions_from_action(action),
        metadata={"source": "gateway_request", "action": action},
    )


def infer_remote_action_from_tool_name(tool_name: str) -> str:
    normalized = tool_name.strip().lower() if isinstance(tool_name, str) else ""
    if any(marker in normalized for marker in ("create", "update", "delete", "write", "push", "commit", "merge", "comment", "send")):
        return ACTION_WRITE
    if any(marker in normalized for marker in ("connect", "auth", "login")):
        return ACTION_CONNECT
    if any(marker in normalized for marker in ("read", "search", "list", "get", "fetch")):
        return ACTION_READ
    return ACTION_INVOKE


def infer_requested_permissions_from_action(action: str) -> list[str]:
    if action == ACTION_WRITE:
        return ["write"]
    if action == ACTION_READ:
        return ["read"]
    if action == ACTION_CONNECT:
        return ["oauth_required"]
    return []


def execute_cloud_mcp_remote_connection(
    request: CloudMCPRemoteCallRequest,
    *,
    status: CloudMCPRemoteConnectionStatus | None = None,
) -> CloudMCPRemoteCallResult:
    if not request.service_id.strip() or not request.tool_name.strip():
        return _remote_error(request, CLOUD_MCP_REMOTE_REQUEST_INVALID, "Cloud MCP remote request is invalid.")
    status = status or build_cloud_mcp_remote_connection_status()
    if not status.enabled or not status.ready:
        return _remote_error(request, CLOUD_MCP_REMOTE_CONNECTION_NOT_READY, "Cloud MCP remote connection is not ready.")

    lookup = get_cloud_mcp_catalog_service(request.service_id)
    if not lookup.found or lookup.service is None:
        return _remote_error(request, CLOUD_MCP_REMOTE_SERVICE_NOT_FOUND, "Cloud MCP remote service was not found.", remote_checked=True)
    action = str(request.metadata.get("action") or infer_remote_action_from_tool_name(request.tool_name))
    requested_permissions = request.requested_permissions or infer_requested_permissions_from_action(action)
    decision = evaluate_cloud_mcp_permission(
        CloudMCPPermissionEvaluationRequest(
            service_id=request.service_id,
            action=action,
            requested_permissions=requested_permissions,
            account_connected=request.account_connected,
            user_confirmed=request.user_confirmed,
            context={"arguments": sanitize_cloud_mcp_value(dict(request.arguments))},
        )
    )
    decision_dict = cloud_mcp_permission_decision_to_dict(decision)
    if not decision.allowed:
        code = str(decision.reason or CLOUD_MCP_REMOTE_PERMISSION_DENIED)
        if code == CLOUD_MCP_ACCOUNT_CONNECTION_REQUIRED:
            code = CLOUD_MCP_REMOTE_ACCOUNT_CONNECTION_REQUIRED
        if code == CLOUD_MCP_USER_CONFIRMATION_REQUIRED:
            code = CLOUD_MCP_REMOTE_USER_CONFIRMATION_REQUIRED
        return CloudMCPRemoteCallResult(
            success=False,
            service_id=request.service_id,
            tool_name=request.tool_name,
            error={"code": code, "message": "Cloud MCP remote permission denied."},
            request_id=request.request_id,
            permission_decision=decision_dict,
            remote_checked=True,
            executed=False,
            metadata={"permission_allowed": False, "remote_connection": "foundation", "placeholder": True},
        )

    return CloudMCPRemoteCallResult(
        success=False,
        service_id=request.service_id,
        tool_name=request.tool_name,
        data={"status": "placeholder", "remote_execution_enabled": False},
        error={"code": CLOUD_MCP_REMOTE_EXECUTION_NOT_READY, "message": "Cloud MCP remote execution is not ready."},
        request_id=request.request_id,
        permission_decision=decision_dict,
        remote_checked=True,
        executed=False,
        metadata={"permission_allowed": True, "remote_connection": "foundation", "placeholder": True},
    )


def _remote_error(
    request: CloudMCPRemoteCallRequest,
    code: str,
    message: str,
    *,
    remote_checked: bool = False,
) -> CloudMCPRemoteCallResult:
    return CloudMCPRemoteCallResult(
        success=False,
        service_id=request.service_id,
        tool_name=request.tool_name,
        error={"code": code, "message": message},
        request_id=request.request_id,
        remote_checked=remote_checked,
        executed=False,
        metadata={"permission_allowed": False, "remote_connection": "foundation", "placeholder": True},
    )
