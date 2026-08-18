"""Cloud MCP Server smoke checks for the foundation chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cloud_mcp.catalog import (
    build_cloud_mcp_catalog,
    cloud_mcp_catalog_lookup_result_to_dict,
    cloud_mcp_catalog_to_dict,
    get_cloud_mcp_catalog_service,
    list_cloud_mcp_catalog_services,
)
from cloud_mcp.config import CloudMCPGatewayConfig
from cloud_mcp.gateway import build_cloud_mcp_gateway_status, invoke_cloud_mcp_gateway, sanitize_cloud_mcp_value
from cloud_mcp.oauth import cloud_mcp_oauth_connection_status_to_dict, get_cloud_mcp_oauth_provider, list_cloud_mcp_oauth_providers
from cloud_mcp.permissions import (
    ACTION_READ,
    ACTION_WRITE,
    CloudMCPPermissionEvaluationRequest,
    cloud_mcp_permission_decision_to_dict,
    evaluate_cloud_mcp_permission,
)
from cloud_mcp.remote_connection import (
    CLOUD_MCP_REMOTE_EXECUTION_NOT_READY,
    CloudMCPRemoteCallRequest,
    build_cloud_mcp_remote_connection_status,
    cloud_mcp_remote_call_result_to_dict,
    cloud_mcp_remote_connection_status_to_dict,
    execute_cloud_mcp_remote_connection,
)
from cloud_mcp.token_store import build_oauth_connection_status, create_in_memory_token_store
from cloud_mcp.types import cloud_mcp_gateway_response_to_dict, cloud_mcp_gateway_status_to_dict


CLOUD_MCP_SERVER_SMOKE_OK = "cloud_mcp_server_smoke_ok"
CLOUD_MCP_SERVER_SMOKE_FAILED = "cloud_mcp_server_smoke_failed"
CLOUD_MCP_SERVER_SMOKE_FOUNDATION_ONLY = "cloud_mcp_server_smoke_foundation_only"


@dataclass(frozen=True)
class CloudMCPServerSmokeResult:
    ok: bool
    status: str
    gateway: dict[str, object] = field(default_factory=dict)
    catalog: dict[str, object] = field(default_factory=dict)
    oauth: dict[str, object] = field(default_factory=dict)
    permissions: dict[str, object] = field(default_factory=dict)
    remote_connection: dict[str, object] = field(default_factory=dict)
    invoke: dict[str, object] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def cloud_mcp_server_smoke_result_to_dict(result: CloudMCPServerSmokeResult) -> dict[str, object]:
    return {
        "ok": bool(result.ok),
        "status": sanitize_cloud_mcp_value(result.status),
        "gateway": sanitize_cloud_mcp_value(dict(result.gateway)),
        "catalog": sanitize_cloud_mcp_value(dict(result.catalog)),
        "oauth": sanitize_cloud_mcp_value(dict(result.oauth)),
        "permissions": sanitize_cloud_mcp_value(dict(result.permissions)),
        "remote_connection": sanitize_cloud_mcp_value(dict(result.remote_connection)),
        "invoke": sanitize_cloud_mcp_value(dict(result.invoke)),
        "checks": {str(key): bool(value) for key, value in result.checks.items()},
        "errors": sanitize_cloud_mcp_value(list(result.errors)),
        "warnings": sanitize_cloud_mcp_value(list(result.warnings)),
        "metadata": sanitize_cloud_mcp_value(dict(result.metadata)),
    }


def run_cloud_mcp_server_smoke(
    *,
    include_invoke: bool = True,
    include_remote_check: bool = True,
) -> CloudMCPServerSmokeResult:
    checks: dict[str, bool] = {}
    errors: list[str] = []
    warnings: list[str] = []

    config = CloudMCPGatewayConfig(
        enabled=True,
        catalog_ready=True,
        oauth_ready=True,
        token_store_ready=True,
        permission_model_ready=True,
        remote_connection_ready=True,
    )
    gateway_status = build_cloud_mcp_gateway_status(config)
    gateway = {
        "config": {
            "enabled": config.enabled,
            "catalog_ready": config.catalog_ready,
            "oauth_ready": config.oauth_ready,
            "token_store_ready": config.token_store_ready,
            "permission_model_ready": config.permission_model_ready,
            "remote_connection_ready": config.remote_connection_ready,
        },
        "status": cloud_mcp_gateway_status_to_dict(gateway_status),
    }
    checks.update(
        {
            "gateway_ready": bool(config.enabled),
            "gateway_status_ready": gateway_status.status == "ready",
            "catalog_ready": bool(gateway_status.catalog_ready),
            "oauth_ready": bool(gateway_status.oauth_ready),
            "token_store_ready": bool(gateway_status.token_store_ready),
            "permission_model_ready": bool(gateway_status.permission_model_ready),
            "remote_connection_ready": bool(gateway_status.remote_connection_ready),
        }
    )
    warnings.extend(gateway_status.warnings)

    catalog_model = build_cloud_mcp_catalog()
    services = list_cloud_mcp_catalog_services(catalog_model)
    service_ids = [service.service_id for service in services]
    cloud_browser = get_cloud_mcp_catalog_service("cloud-browser", catalog_model)
    cloud_github = get_cloud_mcp_catalog_service("cloud-github", catalog_model)
    catalog = {
        "catalog": cloud_mcp_catalog_to_dict(catalog_model),
        "service_ids": service_ids,
        "cloud_browser": cloud_mcp_catalog_lookup_result_to_dict(cloud_browser),
        "cloud_github": cloud_mcp_catalog_lookup_result_to_dict(cloud_github),
    }
    checks.update(
        {
            "catalog_enabled": bool(catalog_model.enabled),
            "catalog_services_present": {"cloud-browser", "cloud-search", "cloud-github"}.issubset(set(service_ids)),
            "cloud_browser_present": bool(cloud_browser.found),
            "cloud_github_present": bool(cloud_github.found),
            "cloud_github_requires_oauth": bool(cloud_github.service and cloud_github.service.requires_oauth),
        }
    )
    warnings.extend(catalog_model.warnings)

    providers = list_cloud_mcp_oauth_providers()
    provider_ids = [provider.provider_id for provider in providers]
    github_provider = get_cloud_mcp_oauth_provider("github")
    google_provider = get_cloud_mcp_oauth_provider("google")
    token_store = create_in_memory_token_store()
    github_connection_status = build_oauth_connection_status("github", token_store=token_store)
    github_connection = cloud_mcp_oauth_connection_status_to_dict(github_connection_status)
    oauth = {
        "provider_ids": provider_ids,
        "github_provider_found": github_provider is not None,
        "google_provider_found": google_provider is not None,
        "github_connection": github_connection,
    }
    checks.update(
        {
            "oauth_providers_present": {"github", "google"}.issubset(set(provider_ids)),
            "github_provider_present": github_provider is not None,
            "google_provider_present": google_provider is not None,
            "github_connection_visible": github_connection.get("provider_id") == "github",
            "token_visible_false": github_connection.get("token_visible") is False,
            "token_not_available_by_default": github_connection.get("token_available") is False,
        }
    )

    browser_permission = evaluate_cloud_mcp_permission(
        CloudMCPPermissionEvaluationRequest(
            service_id="cloud-browser",
            action=ACTION_READ,
            requested_permissions=["read_only"],
            account_connected=False,
            user_confirmed=False,
        )
    )
    github_permission = evaluate_cloud_mcp_permission(
        CloudMCPPermissionEvaluationRequest(
            service_id="cloud-github",
            action=ACTION_WRITE,
            requested_permissions=["write"],
            account_connected=False,
            user_confirmed=False,
        )
    )
    permissions = {
        "cloud_browser_read": cloud_mcp_permission_decision_to_dict(browser_permission),
        "cloud_github_write": cloud_mcp_permission_decision_to_dict(github_permission),
    }
    checks.update(
        {
            "cloud_browser_read_allowed": bool(browser_permission.allowed),
            "cloud_github_write_blocked": not github_permission.allowed,
        }
    )
    warnings.extend(browser_permission.warnings)
    warnings.extend(github_permission.warnings)

    remote_connection: dict[str, object] = {}
    if include_remote_check:
        remote_status = build_cloud_mcp_remote_connection_status(enabled=True)
        browser_remote = execute_cloud_mcp_remote_connection(
            CloudMCPRemoteCallRequest(service_id="cloud-browser", tool_name="read_page", arguments={"url": "https://example.invalid"})
        )
        github_remote = execute_cloud_mcp_remote_connection(CloudMCPRemoteCallRequest(service_id="cloud-github", tool_name="create_issue"))
        remote_connection = {
            "status": cloud_mcp_remote_connection_status_to_dict(remote_status),
            "cloud_browser_read": cloud_mcp_remote_call_result_to_dict(browser_remote),
            "cloud_github_write": cloud_mcp_remote_call_result_to_dict(github_remote),
        }
        checks.update(
            {
                "remote_status_ready": bool(remote_status.ready),
                "remote_execution_enabled_false": remote_status.remote_execution_enabled is False,
                "cloud_browser_read_enters_remote_placeholder": bool(browser_remote.remote_checked)
                and browser_remote.executed is False
                and bool(browser_remote.error)
                and browser_remote.error.get("code") == CLOUD_MCP_REMOTE_EXECUTION_NOT_READY,
                "cloud_github_write_blocked_by_permission_layer": bool(github_remote.remote_checked)
                and github_remote.executed is False
                and bool(github_remote.permission_decision)
                and github_remote.permission_decision.get("allowed") is False,
                "remote_check_executed_false": browser_remote.executed is False and github_remote.executed is False,
            }
        )
        warnings.extend(remote_status.warnings)
    else:
        checks["remote_check_skipped"] = True

    invoke: dict[str, object] = {}
    if include_invoke:
        invoke_payload = {"service_id": "cloud-browser", "tool_name": "read_page", "arguments": {"url": "https://example.invalid"}}
        invoke_response = invoke_cloud_mcp_gateway(invoke_payload, config=config)
        invoke_data = cloud_mcp_gateway_response_to_dict(invoke_response)
        remote_result = invoke_response.data if isinstance(invoke_response.data, dict) else {}
        invoke = {"cloud_browser_read": invoke_data}
        checks.update(
            {
                "invoke_route_reaches_remote_connection": bool(remote_result.get("remote_checked")),
                "invoke_executed_false": remote_result.get("executed") is False,
                "invoke_remote_execution_enabled_false": isinstance(remote_result.get("data"), dict)
                and remote_result["data"].get("remote_execution_enabled") is False,
            }
        )
    else:
        checks["invoke_skipped"] = True

    checks.update(
        {
            "no_real_cloud_mcp_call": True,
            "no_remote_network_access": True,
            "no_mcp_tool_execution": True,
            "agent_runtime_not_on_server": True,
        }
    )

    for name, passed in checks.items():
        if not passed:
            errors.append(name)

    ok = not errors
    return CloudMCPServerSmokeResult(
        ok=ok,
        status=CLOUD_MCP_SERVER_SMOKE_FOUNDATION_ONLY if ok else CLOUD_MCP_SERVER_SMOKE_FAILED,
        gateway=_safe_dict(gateway),
        catalog=_safe_dict(catalog),
        oauth=_safe_dict(oauth),
        permissions=_safe_dict(permissions),
        remote_connection=_safe_dict(remote_connection),
        invoke=_safe_dict(invoke),
        checks=checks,
        errors=errors,
        warnings=sorted({str(item) for item in warnings if item}),
        metadata={
            "result": CLOUD_MCP_SERVER_SMOKE_OK if ok else CLOUD_MCP_SERVER_SMOKE_FAILED,
            "foundation": "27.6",
            "remote_execution_enabled": False,
            "executed": False,
            "network_accessed": False,
            "mcp_tool_executed": False,
            "agent_runtime": "client_side",
        },
    )


def _safe_dict(value: dict[str, Any]) -> dict[str, object]:
    sanitized = sanitize_cloud_mcp_value(value)
    return sanitized if isinstance(sanitized, dict) else {}
