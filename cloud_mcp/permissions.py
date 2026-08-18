"""Cloud MCP permission model foundation."""

from __future__ import annotations

from dataclasses import dataclass, field

from cloud_mcp.catalog import CloudMCPServiceCatalogItem, get_cloud_mcp_catalog_service, list_cloud_mcp_catalog_services
from cloud_mcp.gateway import sanitize_cloud_mcp_value


CLOUD_MCP_PERMISSION_DENIED = "cloud_mcp_permission_denied"
CLOUD_MCP_PERMISSION_ALLOWED = "cloud_mcp_permission_allowed"
CLOUD_MCP_PERMISSION_MODEL_NOT_READY = "cloud_mcp_permission_model_not_ready"
CLOUD_MCP_ACCOUNT_CONNECTION_REQUIRED = "cloud_mcp_account_connection_required"
CLOUD_MCP_USER_CONFIRMATION_REQUIRED = "cloud_mcp_user_confirmation_required"
CLOUD_MCP_SENSITIVE_PERMISSION_REQUIRED = "cloud_mcp_sensitive_permission_required"
CLOUD_MCP_WRITE_PERMISSION_REQUIRED = "cloud_mcp_write_permission_required"
CLOUD_MCP_SERVICE_PERMISSION_NOT_FOUND = "cloud_mcp_service_permission_not_found"
CLOUD_MCP_REMOTE_EXECUTION_NOT_READY = "cloud_mcp_remote_execution_not_ready"

PERMISSION_KIND_READ = "read"
PERMISSION_KIND_WRITE = "write"
PERMISSION_KIND_NETWORK = "network"
PERMISSION_KIND_ACCOUNT = "account_connection"
PERMISSION_KIND_OAUTH = "oauth"
PERMISSION_KIND_SENSITIVE = "sensitive"

RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"

ACTION_READ = "read"
ACTION_WRITE = "write"
ACTION_CONNECT = "connect"
ACTION_INVOKE = "invoke"

_RISK_ORDER = {RISK_LEVEL_LOW: 0, RISK_LEVEL_MEDIUM: 1, RISK_LEVEL_HIGH: 2}


@dataclass(frozen=True)
class CloudMCPPermission:
    name: str
    description: str
    kind: str = PERMISSION_KIND_READ
    risk_level: str = RISK_LEVEL_LOW
    required: bool = True
    sensitive: bool = False
    requires_oauth: bool = False
    requires_account_connection: bool = False
    requires_user_confirmation: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPPermissionPolicy:
    service_id: str
    permissions: list[CloudMCPPermission] = field(default_factory=list)
    default_action: str = "deny"
    allow_read_without_confirmation: bool = True
    require_confirmation_for_write: bool = True
    require_confirmation_for_sensitive: bool = True
    requires_oauth: bool = False
    oauth_provider: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPPermissionEvaluationRequest:
    service_id: str
    action: str = ACTION_INVOKE
    requested_permissions: list[str] = field(default_factory=list)
    account_connected: bool = False
    user_confirmed: bool = False
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPPermissionDecision:
    allowed: bool
    service_id: str
    action: str
    required_permissions: list[str] = field(default_factory=list)
    missing_permissions: list[str] = field(default_factory=list)
    requires_oauth: bool = False
    requires_account_connection: bool = False
    requires_user_confirmation: bool = False
    sensitive: bool = False
    risk_level: str = RISK_LEVEL_LOW
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def cloud_mcp_permission_to_dict(permission: CloudMCPPermission) -> dict[str, object]:
    return {
        "name": sanitize_cloud_mcp_value(permission.name),
        "description": sanitize_cloud_mcp_value(permission.description),
        "kind": sanitize_cloud_mcp_value(permission.kind),
        "risk_level": sanitize_cloud_mcp_value(permission.risk_level),
        "required": bool(permission.required),
        "sensitive": bool(permission.sensitive),
        "requires_oauth": bool(permission.requires_oauth),
        "requires_account_connection": bool(permission.requires_account_connection),
        "requires_user_confirmation": bool(permission.requires_user_confirmation),
        "metadata": sanitize_cloud_mcp_value(dict(permission.metadata)),
    }


def cloud_mcp_permission_policy_to_dict(policy: CloudMCPPermissionPolicy) -> dict[str, object]:
    return {
        "service_id": sanitize_cloud_mcp_value(policy.service_id),
        "permissions": [cloud_mcp_permission_to_dict(permission) for permission in policy.permissions],
        "default_action": sanitize_cloud_mcp_value(policy.default_action),
        "allow_read_without_confirmation": bool(policy.allow_read_without_confirmation),
        "require_confirmation_for_write": bool(policy.require_confirmation_for_write),
        "require_confirmation_for_sensitive": bool(policy.require_confirmation_for_sensitive),
        "requires_oauth": bool(policy.requires_oauth),
        "oauth_provider": sanitize_cloud_mcp_value(policy.oauth_provider) if policy.oauth_provider is not None else None,
        "metadata": sanitize_cloud_mcp_value(dict(policy.metadata)),
    }


def cloud_mcp_permission_evaluation_request_to_dict(
    request: CloudMCPPermissionEvaluationRequest,
) -> dict[str, object]:
    return {
        "service_id": sanitize_cloud_mcp_value(request.service_id),
        "action": sanitize_cloud_mcp_value(request.action),
        "requested_permissions": sanitize_cloud_mcp_value(list(request.requested_permissions)),
        "account_connected": bool(request.account_connected),
        "user_confirmed": bool(request.user_confirmed),
        "context": sanitize_cloud_mcp_value(dict(request.context)),
    }


def cloud_mcp_permission_decision_to_dict(decision: CloudMCPPermissionDecision) -> dict[str, object]:
    return {
        "allowed": bool(decision.allowed),
        "service_id": sanitize_cloud_mcp_value(decision.service_id),
        "action": sanitize_cloud_mcp_value(decision.action),
        "required_permissions": sanitize_cloud_mcp_value(list(decision.required_permissions)),
        "missing_permissions": sanitize_cloud_mcp_value(list(decision.missing_permissions)),
        "requires_oauth": bool(decision.requires_oauth),
        "requires_account_connection": bool(decision.requires_account_connection),
        "requires_user_confirmation": bool(decision.requires_user_confirmation),
        "sensitive": bool(decision.sensitive),
        "risk_level": sanitize_cloud_mcp_value(decision.risk_level),
        "reason": sanitize_cloud_mcp_value(decision.reason) if decision.reason is not None else None,
        "warnings": sanitize_cloud_mcp_value(list(decision.warnings)),
        "metadata": sanitize_cloud_mcp_value(dict(decision.metadata)),
    }


def _permission_kind(name: str) -> str:
    normalized = name.strip().lower()
    if normalized in {"read", "read_only"}:
        return PERMISSION_KIND_READ
    if normalized == "write":
        return PERMISSION_KIND_WRITE
    if normalized == "network":
        return PERMISSION_KIND_NETWORK
    if normalized == "oauth_required":
        return PERMISSION_KIND_OAUTH
    if normalized == "account_connection":
        return PERMISSION_KIND_ACCOUNT
    if normalized == "sensitive":
        return PERMISSION_KIND_SENSITIVE
    return PERMISSION_KIND_READ


def _highest_risk(permissions: list[CloudMCPPermission]) -> str:
    highest = RISK_LEVEL_LOW
    for permission in permissions:
        if _RISK_ORDER.get(permission.risk_level, 0) > _RISK_ORDER.get(highest, 0):
            highest = permission.risk_level
    return highest


def build_permission_policy_from_catalog_service(
    service: CloudMCPServiceCatalogItem,
) -> CloudMCPPermissionPolicy:
    permissions: list[CloudMCPPermission] = []
    for item in service.permissions:
        kind = _permission_kind(item.name)
        is_write = kind == PERMISSION_KIND_WRITE
        is_oauth = kind == PERMISSION_KIND_OAUTH
        is_sensitive = bool(item.sensitive or item.risk_level == RISK_LEVEL_HIGH or kind == PERMISSION_KIND_SENSITIVE)
        permissions.append(
            CloudMCPPermission(
                name=item.name,
                description=item.description,
                kind=kind,
                risk_level=item.risk_level,
                required=item.required,
                sensitive=is_sensitive,
                requires_oauth=is_oauth or bool(service.requires_oauth),
                requires_account_connection=is_oauth or bool(service.requires_oauth),
                requires_user_confirmation=bool(is_sensitive or is_write or item.risk_level == RISK_LEVEL_HIGH),
                metadata={"source": "catalog"},
            )
        )
    return CloudMCPPermissionPolicy(
        service_id=service.service_id,
        permissions=permissions,
        requires_oauth=bool(service.requires_oauth),
        oauth_provider=service.oauth_provider,
        metadata={"display_name": service.display_name, "category": service.category, "permission_model": "ready"},
    )


def get_cloud_mcp_service_permission_policy(
    service_id: str,
) -> CloudMCPPermissionPolicy | None:
    try:
        result = get_cloud_mcp_catalog_service(service_id)
        if not result.found or result.service is None:
            return None
        return build_permission_policy_from_catalog_service(result.service)
    except Exception:  # noqa: BLE001
        return None


def _select_permissions(policy: CloudMCPPermissionPolicy, requested: list[str]) -> list[CloudMCPPermission]:
    if not requested:
        return list(policy.permissions)
    requested_set = {item.strip().lower() for item in requested if isinstance(item, str) and item.strip()}
    selected = [permission for permission in policy.permissions if permission.name.strip().lower() in requested_set]
    return selected or list(policy.permissions)


def evaluate_cloud_mcp_permission(
    request: CloudMCPPermissionEvaluationRequest,
    *,
    policy: CloudMCPPermissionPolicy | None = None,
) -> CloudMCPPermissionDecision:
    policy = policy or get_cloud_mcp_service_permission_policy(request.service_id)
    if policy is None:
        return CloudMCPPermissionDecision(
            allowed=False,
            service_id=request.service_id,
            action=request.action,
            reason=CLOUD_MCP_SERVICE_PERMISSION_NOT_FOUND,
            warnings=[CLOUD_MCP_REMOTE_EXECUTION_NOT_READY],
            metadata={"context": sanitize_cloud_mcp_value(dict(request.context))},
        )

    permissions = _select_permissions(policy, request.requested_permissions)
    required_permissions = [permission.name for permission in permissions if permission.required]
    risk_level = _highest_risk(permissions)
    sensitive = any(permission.sensitive for permission in permissions)
    has_write = request.action == ACTION_WRITE or any(permission.kind == PERMISSION_KIND_WRITE for permission in permissions)
    needs_confirmation = any(permission.requires_user_confirmation for permission in permissions)
    warnings = [CLOUD_MCP_REMOTE_EXECUTION_NOT_READY]

    if policy.requires_oauth and not request.account_connected:
        return CloudMCPPermissionDecision(
            allowed=False,
            service_id=policy.service_id,
            action=request.action,
            required_permissions=required_permissions,
            missing_permissions=required_permissions,
            requires_oauth=True,
            requires_account_connection=True,
            requires_user_confirmation=bool(needs_confirmation),
            sensitive=sensitive,
            risk_level=risk_level,
            reason=CLOUD_MCP_ACCOUNT_CONNECTION_REQUIRED,
            warnings=warnings,
            metadata={"context": sanitize_cloud_mcp_value(dict(request.context))},
        )

    if sensitive and not request.user_confirmed:
        return CloudMCPPermissionDecision(
            allowed=False,
            service_id=policy.service_id,
            action=request.action,
            required_permissions=required_permissions,
            missing_permissions=[permission.name for permission in permissions if permission.sensitive],
            requires_oauth=bool(policy.requires_oauth),
            requires_account_connection=bool(policy.requires_oauth),
            requires_user_confirmation=True,
            sensitive=True,
            risk_level=risk_level,
            reason=CLOUD_MCP_SENSITIVE_PERMISSION_REQUIRED,
            warnings=warnings,
            metadata={"context": sanitize_cloud_mcp_value(dict(request.context))},
        )

    if has_write and policy.require_confirmation_for_write and not request.user_confirmed:
        return CloudMCPPermissionDecision(
            allowed=False,
            service_id=policy.service_id,
            action=request.action,
            required_permissions=required_permissions,
            missing_permissions=[permission.name for permission in permissions if permission.kind == PERMISSION_KIND_WRITE],
            requires_oauth=bool(policy.requires_oauth),
            requires_account_connection=bool(policy.requires_oauth),
            requires_user_confirmation=True,
            sensitive=sensitive,
            risk_level=risk_level,
            reason=CLOUD_MCP_USER_CONFIRMATION_REQUIRED,
            warnings=warnings,
            metadata={"context": sanitize_cloud_mcp_value(dict(request.context))},
        )

    return CloudMCPPermissionDecision(
        allowed=True,
        service_id=policy.service_id,
        action=request.action,
        required_permissions=required_permissions,
        requires_oauth=bool(policy.requires_oauth),
        requires_account_connection=bool(policy.requires_oauth),
        requires_user_confirmation=bool(needs_confirmation),
        sensitive=sensitive,
        risk_level=risk_level,
        reason=CLOUD_MCP_PERMISSION_ALLOWED,
        warnings=warnings,
        metadata={"context": sanitize_cloud_mcp_value(dict(request.context))},
    )


def build_cloud_mcp_permission_summary(
    *,
    include_disabled: bool = True,
) -> dict[str, object]:
    services: list[dict[str, object]] = []
    for service in list_cloud_mcp_catalog_services(include_disabled=include_disabled):
        policy = build_permission_policy_from_catalog_service(service)
        permissions = list(policy.permissions)
        services.append(
            {
                "service_id": sanitize_cloud_mcp_value(service.service_id),
                "display_name": sanitize_cloud_mcp_value(service.display_name),
                "requires_oauth": bool(policy.requires_oauth),
                "oauth_provider": sanitize_cloud_mcp_value(policy.oauth_provider) if policy.oauth_provider is not None else None,
                "permission_count": len(permissions),
                "highest_risk_level": sanitize_cloud_mcp_value(_highest_risk(permissions)),
                "requires_user_confirmation": any(permission.requires_user_confirmation for permission in permissions),
                "permissions": [cloud_mcp_permission_to_dict(permission) for permission in permissions],
            }
        )
    return {
        "enabled": True,
        "version": "0.1.0",
        "services": services,
        "warnings": [CLOUD_MCP_REMOTE_EXECUTION_NOT_READY],
    }
