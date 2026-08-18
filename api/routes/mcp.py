"""MCP marketplace and install manager API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.deps import safe_error
from api.schemas import ApiResponse, MCPInstallRequest, MCPPermissionUpdateRequest
from core.mcp_dependency_diagnostics import build_dependency_report
from core.mcp_install_manager import MCPInstallManager
from core.mcp_marketplace_catalog import MCPMarketplaceCatalog
from core.mcp_permission_panel import build_mcp_permission_snapshot, build_server_permission_detail
from core.mcp_runtime_manager import MCPRuntimeManager, get_mcp_runtime_manager


router = APIRouter(prefix="/mcp", tags=["mcp"])


def get_catalog() -> MCPMarketplaceCatalog:
    return MCPMarketplaceCatalog()


def get_install_manager() -> MCPInstallManager:
    return MCPInstallManager()


def get_runtime_manager() -> MCPRuntimeManager:
    return get_mcp_runtime_manager()


@router.get("/marketplace", response_model=ApiResponse)
def list_marketplace() -> ApiResponse:
    try:
        catalog = get_catalog()
        catalog.load_catalog()
        manager = get_install_manager()
        installed = manager.list_installed_mcp()
        items = _merge_installed_state(catalog.list_items(), installed)
        return ApiResponse(
            success=True,
            data={
                "items": items,
                "installed": installed,
                "counts": {
                    "catalog_total": len(items),
                    "available": len([item for item in items if item.get("status") == "available"]),
                    "installed": len(installed),
                },
                "errors": catalog.errors,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_marketplace_failed", safe_error(exc))


@router.get("/marketplace/{item_id}", response_model=ApiResponse)
def get_marketplace_item(item_id: str) -> ApiResponse:
    try:
        result = get_catalog().get_item(item_id)
        if not result.get("success"):
            return _error(str(result.get("error_code", "catalog_item_not_found")), "Catalog item not found.")
        return ApiResponse(success=True, data=result["item"])
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_marketplace_item_failed", safe_error(exc))


@router.get("/installed", response_model=ApiResponse)
def list_installed() -> ApiResponse:
    try:
        return ApiResponse(success=True, data={"items": get_install_manager().list_installed_mcp()})
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_installed_list_failed", safe_error(exc))


@router.get("/installed/{server_name:path}", response_model=ApiResponse)
def get_installed(server_name: str) -> ApiResponse:
    try:
        result = get_install_manager().read_installed_mcp(server_name)
        if not result.get("success"):
            return _error(str(result.get("error_code", "mcp_server_not_found")), "Installed MCP server not found.")
        return ApiResponse(success=True, data=result)
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_installed_read_failed", safe_error(exc))


@router.get("/runtime/status", response_model=ApiResponse)
def get_runtime_status() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=get_runtime_manager().status())
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_runtime_status_failed", safe_error(exc))


@router.post("/runtime/reload", response_model=ApiResponse)
def reload_runtime() -> ApiResponse:
    try:
        result = get_runtime_manager().reload()
        return ApiResponse(success=bool(result.get("success", False)), data=result)
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_runtime_reload_failed", safe_error(exc))


@router.get("/runtime/dependencies", response_model=ApiResponse)
def get_runtime_dependencies() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=build_dependency_report())
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(
            success=True,
            data={
                "docker": {"found": False},
                "node": {"found": False},
                "npm": {"found": False},
                "npx": {"found": False},
                "env": {},
                "error": safe_error(exc),
            },
        )


@router.get("/permissions", response_model=ApiResponse)
def get_permissions() -> ApiResponse:
    try:
        return ApiResponse(
            success=True,
            data=build_mcp_permission_snapshot(get_runtime_manager(), install_manager=get_install_manager(), catalog=get_catalog()),
        )
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_permissions_failed", safe_error(exc))


@router.get("/permissions/{server_name}", response_model=ApiResponse)
def get_permission_server(server_name: str) -> ApiResponse:
    try:
        result = build_server_permission_detail(server_name, get_runtime_manager(), install_manager=get_install_manager(), catalog=get_catalog())
        if not result.get("success"):
            return _error(str(result.get("error_code", "mcp_server_not_found")), str(result.get("error", "MCP server not found.")))
        return ApiResponse(success=True, data=result["server"])
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_permissions_failed", safe_error(exc))


@router.post("/permissions/{server_name}", response_model=ApiResponse)
def update_permission_server(server_name: str, request: MCPPermissionUpdateRequest) -> ApiResponse:
    try:
        catalog_error = _validate_catalog_permissions(server_name, request.permissions)
        if catalog_error:
            return catalog_error
        result = get_install_manager().update_mcp_permissions(server_name, request.permissions)
        if not result.get("success"):
            return _error(str(result.get("error_code", "mcp_permission_update_failed")), str(result.get("error", "Permission update failed.")))
        reload_payload = _reload_runtime_after_config_change()
        return ApiResponse(
            success=True,
            data={
                "server_name": server_name,
                "permissions": result.get("permissions", request.permissions),
                "runtime_reload_required": True,
                "hot_reload": True,
                "reload_endpoint": "/mcp/runtime/reload",
                **reload_payload,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_permission_update_failed", safe_error(exc))


@router.post("/marketplace/{item_id}/install", response_model=ApiResponse)
def install_marketplace_item(item_id: str, request: MCPInstallRequest) -> ApiResponse:
    try:
        catalog = get_catalog()
        item_result = catalog.get_item(item_id)
        if not item_result.get("success"):
            return _error(str(item_result.get("error_code", "catalog_item_not_found")), "Catalog item not found.")
        permission_error = _validate_permissions(item_result["item"], request.permissions)
        if permission_error:
            return permission_error
        payload_result = catalog.build_install_payload(item_id, enabled=request.enabled, permissions=request.permissions)
        if not payload_result.get("success"):
            return _error(str(payload_result.get("error_code", "catalog_item_not_installable")), "Catalog item is not installable.")
        manager = get_install_manager()
        install_result = manager.install_mcp(**payload_result["payload"])
        if not install_result.get("success"):
            return _error(str(install_result.get("error_code", "mcp_install_failed")), str(install_result.get("error", "Install failed.")))
        installed = manager.read_installed_mcp(payload_result["payload"]["server_name"])
        reload_payload = _reload_runtime_after_config_change()
        return ApiResponse(
            success=True,
            data={
                "result": install_result,
                "installed": installed if installed.get("success") else None,
                "runtime_reload_required": True,
                "hot_reload": True,
                "reload_endpoint": "/mcp/runtime/reload",
                **reload_payload,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_install_failed", safe_error(exc))


@router.post("/installed/{server_name:path}/enable", response_model=ApiResponse)
def enable_installed(server_name: str) -> ApiResponse:
    return _mutate_installed(server_name, "enable")


@router.post("/installed/{server_name:path}/disable", response_model=ApiResponse)
def disable_installed(server_name: str) -> ApiResponse:
    return _mutate_installed(server_name, "disable")


@router.delete("/installed/{server_name:path}", response_model=ApiResponse)
def uninstall_installed(server_name: str) -> ApiResponse:
    try:
        result = get_install_manager().uninstall_mcp(server_name)
        if not result.get("success"):
            return _error(str(result.get("error_code", "mcp_uninstall_failed")), str(result.get("error", "Uninstall failed.")))
        reload_payload = _reload_runtime_after_config_change()
        return ApiResponse(
            success=True,
            data={**result, "runtime_reload_required": True, "hot_reload": True, "reload_endpoint": "/mcp/runtime/reload", **reload_payload},
        )
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_uninstall_failed", safe_error(exc))


def _mutate_installed(server_name: str, action: str) -> ApiResponse:
    try:
        manager = get_install_manager()
        result = manager.enable_mcp(server_name) if action == "enable" else manager.disable_mcp(server_name)
        if not result.get("success"):
            return _error(str(result.get("error_code", f"mcp_{action}_failed")), str(result.get("error", f"{action} failed.")))
        installed = manager.read_installed_mcp(server_name)
        reload_payload = _reload_runtime_after_config_change()
        return ApiResponse(
            success=True,
            data={
                "result": result,
                "installed": installed if installed.get("success") else None,
                "runtime_reload_required": True,
                "hot_reload": True,
                "reload_endpoint": "/mcp/runtime/reload",
                **reload_payload,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"mcp_{action}_failed", safe_error(exc))


def _merge_installed_state(catalog_items: list[dict], installed_items: list[dict]) -> list[dict]:
    installed_by_server = {item.get("server_name"): item for item in installed_items if item.get("server_name")}
    merged = []
    for item in catalog_items:
        installed = installed_by_server.get(item.get("server_name"))
        merged.append(
            {
                **item,
                "installed": installed is not None,
                "installed_enabled": bool(installed.get("enabled")) if installed is not None and "enabled" in installed else None,
                "installable": bool(item.get("installable")),
            }
        )
    return merged


def _validate_permissions(item: dict, requested: list[str] | None) -> ApiResponse | None:
    if not requested:
        return None
    permissions = item.get("permissions") if isinstance(item.get("permissions"), dict) else {}
    available = permissions.get("available") or permissions.get("default") or []
    available_set = {str(value) for value in available}
    requested_set = {str(value) for value in requested}
    if not requested_set.issubset(available_set):
        return _error("invalid_mcp_permissions", "Requested MCP permissions are not allowed by the catalog item.")
    return None


def _validate_catalog_permissions(server_name: str, requested: list[str]) -> ApiResponse | None:
    if "dangerous" in {str(value).strip().lower() for value in requested}:
        return _error("dangerous_permission_not_allowed", "dangerous permission cannot be enabled from the permission panel.")
    catalog = get_catalog()
    result = catalog.get_item_by_server_name(server_name)
    if not result.get("success"):
        return None
    item = result.get("item") or {}
    permissions = item.get("permissions") if isinstance(item.get("permissions"), dict) else {}
    available = permissions.get("available") or permissions.get("default") or []
    available_set = {str(value) for value in available}
    requested_set = {str(value) for value in requested}
    if available_set and not requested_set.issubset(available_set):
        return _error("invalid_mcp_permissions", "Requested MCP permissions are not allowed by the catalog item.")
    return None


def _reload_runtime_after_config_change() -> dict[str, Any]:
    try:
        result = get_runtime_manager().reload()
    except Exception as exc:  # noqa: BLE001 - config mutations should not be rolled back by reload failures.
        return {"runtime_reloaded": False, "reload_error": safe_error(exc)}
    if result.get("success"):
        return {"runtime_reloaded": True, "reload_result": result}
    return {
        "runtime_reloaded": False,
        "reload_result": result,
        "reload_error": str(result.get("last_reload_error") or "MCP runtime reload failed."),
    }


def _error(code: str, message: str) -> ApiResponse:
    return ApiResponse(success=False, error={"code": code, "message": message})
