"""Cloud MCP Gateway foundation routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from api.deps import safe_error
from api.schemas import ApiResponse
from cloud_mcp.catalog import (
    CLOUD_MCP_ACCOUNT_CONNECTION_NOT_READY,
    CLOUD_MCP_INSTALL_NOT_READY,
    build_cloud_mcp_catalog,
    cloud_mcp_catalog_lookup_result_to_dict,
    cloud_mcp_catalog_to_dict,
    cloud_mcp_install_entry_to_dict,
    cloud_mcp_service_catalog_item_to_dict,
    get_cloud_mcp_catalog_service,
    list_cloud_mcp_catalog_services,
)
from cloud_mcp.gateway import build_cloud_mcp_gateway_health_payload, build_cloud_mcp_gateway_status, invoke_cloud_mcp_gateway
from cloud_mcp.oauth import (
    OAUTH_PROVIDER_NOT_FOUND,
    cloud_mcp_oauth_connection_status_to_dict,
    cloud_mcp_oauth_provider_to_dict,
    get_cloud_mcp_oauth_provider,
    list_cloud_mcp_oauth_providers,
)
from cloud_mcp.permissions import (
    CLOUD_MCP_SERVICE_PERMISSION_NOT_FOUND,
    CloudMCPPermissionEvaluationRequest,
    build_cloud_mcp_permission_summary,
    cloud_mcp_permission_decision_to_dict,
    cloud_mcp_permission_policy_to_dict,
    evaluate_cloud_mcp_permission,
    get_cloud_mcp_service_permission_policy,
)
from cloud_mcp.remote_connection import (
    build_cloud_mcp_remote_connection_status,
    build_remote_call_request_from_gateway_payload,
    cloud_mcp_remote_call_result_to_dict,
    cloud_mcp_remote_connection_status_to_dict,
    execute_cloud_mcp_remote_connection,
)
from cloud_mcp.smoke import cloud_mcp_server_smoke_result_to_dict, run_cloud_mcp_server_smoke
from cloud_mcp.token_store import build_oauth_connection_status, create_in_memory_token_store
from cloud_mcp.types import cloud_mcp_gateway_response_to_dict, cloud_mcp_gateway_status_to_dict


router = APIRouter(prefix="/cloud-mcp", tags=["cloud-mcp-gateway"])


@router.get("/health", response_model=ApiResponse)
def get_cloud_mcp_health() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=build_cloud_mcp_gateway_health_payload())
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_health_failed", "message": safe_error(exc)})


@router.get("/status", response_model=ApiResponse)
def get_cloud_mcp_status() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=cloud_mcp_gateway_status_to_dict(build_cloud_mcp_gateway_status()))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_status_failed", "message": safe_error(exc)})


@router.get("/catalog", response_model=ApiResponse)
def get_cloud_mcp_catalog() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=cloud_mcp_catalog_to_dict(build_cloud_mcp_catalog()))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_catalog_failed", "message": safe_error(exc)})


@router.get("/catalog/services", response_model=ApiResponse)
def get_cloud_mcp_catalog_services(category: str | None = None, include_disabled: bool = True) -> ApiResponse:
    try:
        services = list_cloud_mcp_catalog_services(category=category, include_disabled=include_disabled)
        return ApiResponse(success=True, data=[cloud_mcp_service_catalog_item_to_dict(service) for service in services])
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_catalog_services_failed", "message": safe_error(exc)})


@router.get("/catalog/services/{service_id}", response_model=ApiResponse)
def get_cloud_mcp_catalog_service_route(service_id: str) -> ApiResponse:
    try:
        result = get_cloud_mcp_catalog_service(service_id)
        return ApiResponse(success=True, data=cloud_mcp_catalog_lookup_result_to_dict(result))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_catalog_service_failed", "message": safe_error(exc)})


@router.post("/catalog/services/{service_id}/install", response_model=ApiResponse)
def post_cloud_mcp_catalog_service_install(service_id: str) -> ApiResponse:
    try:
        result = get_cloud_mcp_catalog_service(service_id)
        if not result.found or result.service is None:
            return ApiResponse(success=True, data=cloud_mcp_catalog_lookup_result_to_dict(result))
        return ApiResponse(
            success=True,
            data={
                "success": False,
                "service_id": result.service.service_id,
                "install_entry": cloud_mcp_install_entry_to_dict(result.service.install_entry),
                "error": {"code": CLOUD_MCP_INSTALL_NOT_READY, "message": "Cloud MCP install is not ready."},
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_catalog_install_failed", "message": safe_error(exc)})


@router.get("/oauth/providers", response_model=ApiResponse)
def get_cloud_mcp_oauth_providers(include_disabled: bool = True) -> ApiResponse:
    try:
        providers = list_cloud_mcp_oauth_providers(include_disabled=include_disabled)
        return ApiResponse(success=True, data=[cloud_mcp_oauth_provider_to_dict(provider) for provider in providers])
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_oauth_providers_failed", "message": safe_error(exc)})


@router.get("/oauth/providers/{provider_id}", response_model=ApiResponse)
def get_cloud_mcp_oauth_provider_route(provider_id: str) -> ApiResponse:
    try:
        provider = get_cloud_mcp_oauth_provider(provider_id)
        if provider is None:
            return ApiResponse(success=True, data={"found": False, "error": {"code": OAUTH_PROVIDER_NOT_FOUND, "message": "Cloud MCP OAuth provider was not found."}})
        return ApiResponse(success=True, data={"found": True, "provider": cloud_mcp_oauth_provider_to_dict(provider)})
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_oauth_provider_failed", "message": safe_error(exc)})


@router.get("/oauth/connections", response_model=ApiResponse)
def get_cloud_mcp_oauth_connections() -> ApiResponse:
    try:
        token_store = create_in_memory_token_store()
        statuses = [
            cloud_mcp_oauth_connection_status_to_dict(build_oauth_connection_status(provider.provider_id, token_store=token_store))
            for provider in list_cloud_mcp_oauth_providers()
        ]
        return ApiResponse(success=True, data=statuses)
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_oauth_connections_failed", "message": safe_error(exc)})


@router.get("/oauth/connections/{provider_id}", response_model=ApiResponse)
def get_cloud_mcp_oauth_connection(provider_id: str) -> ApiResponse:
    try:
        token_store = create_in_memory_token_store()
        status = build_oauth_connection_status(provider_id, token_store=token_store)
        return ApiResponse(success=True, data=cloud_mcp_oauth_connection_status_to_dict(status))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_oauth_connection_failed", "message": safe_error(exc)})


@router.post("/oauth/providers/{provider_id}/connect", response_model=ApiResponse)
def post_cloud_mcp_oauth_provider_connect(provider_id: str) -> ApiResponse:
    try:
        provider = get_cloud_mcp_oauth_provider(provider_id)
        if provider is None:
            return ApiResponse(success=True, data={"success": False, "error": {"code": OAUTH_PROVIDER_NOT_FOUND, "message": "Cloud MCP OAuth provider was not found."}})
        return ApiResponse(
            success=True,
            data={
                "success": False,
                "provider_id": provider.provider_id,
                "error": {"code": CLOUD_MCP_ACCOUNT_CONNECTION_NOT_READY, "message": "Cloud MCP account connection is not ready."},
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_oauth_connect_failed", "message": safe_error(exc)})


@router.delete("/oauth/connections/{provider_id}", response_model=ApiResponse)
def delete_cloud_mcp_oauth_connection(provider_id: str) -> ApiResponse:
    try:
        token_store = create_in_memory_token_store()
        deleted = token_store.delete_token(provider_id, "default")
        return ApiResponse(success=True, data={"provider_id": provider_id, "deleted": deleted, "token_visible": False})
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_oauth_delete_failed", "message": safe_error(exc)})


@router.get("/permissions", response_model=ApiResponse)
def get_cloud_mcp_permissions() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=build_cloud_mcp_permission_summary())
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_permissions_failed", "message": safe_error(exc)})


@router.get("/permissions/services/{service_id}", response_model=ApiResponse)
def get_cloud_mcp_permission_service_policy(service_id: str) -> ApiResponse:
    try:
        policy = get_cloud_mcp_service_permission_policy(service_id)
        if policy is None:
            return ApiResponse(success=True, data={"found": False, "error": {"code": CLOUD_MCP_SERVICE_PERMISSION_NOT_FOUND, "message": "Cloud MCP service permission policy was not found."}})
        return ApiResponse(success=True, data={"found": True, "policy": cloud_mcp_permission_policy_to_dict(policy)})
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_permission_policy_failed", "message": safe_error(exc)})


@router.post("/permissions/evaluate", response_model=ApiResponse)
def post_cloud_mcp_permission_evaluate(payload: Any = Body(...)) -> ApiResponse:
    try:
        request = _parse_permission_evaluation_payload(payload)
        decision = evaluate_cloud_mcp_permission(request)
        return ApiResponse(success=True, data=cloud_mcp_permission_decision_to_dict(decision))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_permission_evaluate_failed", "message": safe_error(exc)})


def _parse_permission_evaluation_payload(payload: Any) -> CloudMCPPermissionEvaluationRequest:
    if not isinstance(payload, dict):
        return CloudMCPPermissionEvaluationRequest(service_id="")
    requested = payload.get("requested_permissions", [])
    if not isinstance(requested, list):
        requested = []
    context = payload.get("context", {})
    if not isinstance(context, dict):
        context = {}
    return CloudMCPPermissionEvaluationRequest(
        service_id=str(payload.get("service_id", "") or "").strip(),
        action=str(payload.get("action", "invoke") or "invoke").strip() or "invoke",
        requested_permissions=[str(item) for item in requested if isinstance(item, str)],
        account_connected=bool(payload.get("account_connected", False)),
        user_confirmed=bool(payload.get("user_confirmed", False)),
        context=context,
    )


@router.get("/remote/status", response_model=ApiResponse)
def get_cloud_mcp_remote_status() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=cloud_mcp_remote_connection_status_to_dict(build_cloud_mcp_remote_connection_status()))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_remote_status_failed", "message": safe_error(exc)})


@router.post("/remote/check", response_model=ApiResponse)
def post_cloud_mcp_remote_check(payload: Any = Body(...)) -> ApiResponse:
    try:
        request = build_remote_call_request_from_gateway_payload(payload)
        if request is None:
            from cloud_mcp.remote_connection import CloudMCPRemoteCallRequest

            request = CloudMCPRemoteCallRequest(service_id="", tool_name="")
        result = execute_cloud_mcp_remote_connection(request)
        return ApiResponse(success=True, data=cloud_mcp_remote_call_result_to_dict(result))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_remote_check_failed", "message": safe_error(exc)})


@router.post("/invoke", response_model=ApiResponse)
def post_cloud_mcp_invoke(payload: Any = Body(...)) -> ApiResponse:
    try:
        response = invoke_cloud_mcp_gateway(payload)
        return ApiResponse(success=True, data=cloud_mcp_gateway_response_to_dict(response))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_invoke_failed", "message": safe_error(exc)})


@router.get("/smoke", response_model=ApiResponse)
def get_cloud_mcp_server_smoke(include_invoke: bool = True, include_remote_check: bool = True) -> ApiResponse:
    try:
        result = run_cloud_mcp_server_smoke(include_invoke=include_invoke, include_remote_check=include_remote_check)
        return ApiResponse(success=True, data=cloud_mcp_server_smoke_result_to_dict(result))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_server_smoke_failed", "message": safe_error(exc)})


@router.post("/smoke/run", response_model=ApiResponse)
def post_cloud_mcp_server_smoke_run(payload: Any = Body(None)) -> ApiResponse:
    try:
        include_invoke = True
        include_remote_check = True
        if isinstance(payload, dict):
            include_invoke = bool(payload.get("include_invoke", True))
            include_remote_check = bool(payload.get("include_remote_check", True))
        result = run_cloud_mcp_server_smoke(include_invoke=include_invoke, include_remote_check=include_remote_check)
        return ApiResponse(success=True, data=cloud_mcp_server_smoke_result_to_dict(result))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "cloud_mcp_server_smoke_failed", "message": safe_error(exc)})
