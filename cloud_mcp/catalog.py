"""Static Cloud MCP Catalog foundation."""

from __future__ import annotations

from dataclasses import dataclass, field

from cloud_mcp.gateway import sanitize_cloud_mcp_value


CLOUD_MCP_SERVICE_NOT_FOUND = "cloud_mcp_service_not_found"
CLOUD_MCP_INSTALL_NOT_READY = "cloud_mcp_install_not_ready"
CLOUD_MCP_OAUTH_NOT_READY = "cloud_mcp_oauth_not_ready"
CLOUD_MCP_ACCOUNT_CONNECTION_NOT_READY = "cloud_mcp_account_connection_not_ready"
CLOUD_MCP_REMOTE_EXECUTION_NOT_READY = "cloud_mcp_remote_execution_not_ready"


@dataclass(frozen=True)
class CloudMCPCatalogPermission:
    name: str
    description: str
    risk_level: str = "low"
    required: bool = True
    sensitive: bool = False


@dataclass(frozen=True)
class CloudMCPInstallEntry:
    type: str = "cloud_gateway"
    label: str = "Connect"
    enabled: bool = False
    reason: str = CLOUD_MCP_REMOTE_EXECUTION_NOT_READY
    path: str | None = None


@dataclass(frozen=True)
class CloudMCPServiceCatalogItem:
    service_id: str
    display_name: str
    description: str
    category: str
    version: str = "0.1.0"
    status: str = "preview"
    enabled: bool = False
    official: bool = True
    requires_oauth: bool = False
    oauth_provider: str | None = None
    permissions: list[CloudMCPCatalogPermission] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    install_entry: CloudMCPInstallEntry = field(default_factory=CloudMCPInstallEntry)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPCatalog:
    enabled: bool
    version: str = "0.1.0"
    services: list[CloudMCPServiceCatalogItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CloudMCPCatalogLookupResult:
    found: bool
    service: CloudMCPServiceCatalogItem | None = None
    error: dict[str, object] | None = None


def default_cloud_mcp_catalog_services() -> list[CloudMCPServiceCatalogItem]:
    return [
        CloudMCPServiceCatalogItem(
            service_id="cloud-browser",
            display_name="Cloud Browser",
            description="Cloud browser service metadata placeholder.",
            category="browser",
            permissions=[
                CloudMCPCatalogPermission(name="network", description="May read public web pages through a future cloud gateway."),
                CloudMCPCatalogPermission(name="read_only", description="Read-only browser result access."),
            ],
            tags=["browser", "network", "read_only"],
            metadata={"foundation": "27.5", "runtime": "not_connected", "permission_model": "ready", "remote_connection": "foundation"},
        ),
        CloudMCPServiceCatalogItem(
            service_id="cloud-search",
            display_name="Cloud Search",
            description="Cloud search service metadata placeholder.",
            category="search",
            permissions=[
                CloudMCPCatalogPermission(name="network", description="May query public search sources through a future cloud gateway."),
                CloudMCPCatalogPermission(name="read_only", description="Read-only search result access."),
            ],
            tags=["search", "network", "read_only"],
            metadata={"foundation": "27.5", "runtime": "not_connected", "permission_model": "ready", "remote_connection": "foundation"},
        ),
        CloudMCPServiceCatalogItem(
            service_id="cloud-github",
            display_name="Cloud GitHub",
            description="Future GitHub Cloud MCP service metadata placeholder.",
            category="developer",
            requires_oauth=True,
            oauth_provider="github",
            permissions=[
                CloudMCPCatalogPermission(name="oauth_required", description="Requires a future account connection before use.", risk_level="medium"),
                CloudMCPCatalogPermission(name="read", description="Read repository metadata after a future account connection.", risk_level="medium"),
                CloudMCPCatalogPermission(name="write", description="May write repository data only after explicit future permission.", risk_level="high", sensitive=True),
            ],
            tags=["github", "developer", "account_connection"],
            install_entry=CloudMCPInstallEntry(reason=CLOUD_MCP_ACCOUNT_CONNECTION_NOT_READY),
            metadata={"foundation": "27.5", "runtime": "not_connected", "provider": "github", "oauth_foundation": "ready", "token_store": "server_side", "permission_model": "ready", "remote_connection": "foundation"},
        ),
    ]


def build_cloud_mcp_catalog(
    *,
    enabled: bool | None = None,
) -> CloudMCPCatalog:
    if enabled is None:
        from config.settings import settings

        enabled = bool(getattr(settings, "cloud_mcp_catalog_enabled", True))
    warnings = [CLOUD_MCP_ACCOUNT_CONNECTION_NOT_READY, CLOUD_MCP_REMOTE_EXECUTION_NOT_READY]
    return CloudMCPCatalog(enabled=bool(enabled), services=default_cloud_mcp_catalog_services(), warnings=warnings)


def list_cloud_mcp_catalog_services(
    catalog: CloudMCPCatalog | None = None,
    *,
    category: str | None = None,
    include_disabled: bool = True,
) -> list[CloudMCPServiceCatalogItem]:
    try:
        catalog = catalog or build_cloud_mcp_catalog()
        services = list(catalog.services)
        if category:
            expected = category.strip().lower()
            services = [service for service in services if service.category.lower() == expected]
        if not include_disabled:
            services = [service for service in services if service.enabled]
        return services
    except Exception:  # noqa: BLE001
        return []


def get_cloud_mcp_catalog_service(
    service_id: str,
    catalog: CloudMCPCatalog | None = None,
) -> CloudMCPCatalogLookupResult:
    try:
        if not isinstance(service_id, str) or not service_id.strip():
            return CloudMCPCatalogLookupResult(found=False, error={"code": CLOUD_MCP_SERVICE_NOT_FOUND, "message": "Cloud MCP service was not found."})
        expected = service_id.strip().lower()
        for service in list_cloud_mcp_catalog_services(catalog):
            if service.service_id.lower() == expected:
                return CloudMCPCatalogLookupResult(found=True, service=service)
        return CloudMCPCatalogLookupResult(found=False, error={"code": CLOUD_MCP_SERVICE_NOT_FOUND, "message": "Cloud MCP service was not found."})
    except Exception:  # noqa: BLE001
        return CloudMCPCatalogLookupResult(found=False, error={"code": CLOUD_MCP_SERVICE_NOT_FOUND, "message": "Cloud MCP service lookup failed."})


def cloud_mcp_catalog_permission_to_dict(item: CloudMCPCatalogPermission) -> dict[str, object]:
    return {
        "name": sanitize_cloud_mcp_value(item.name),
        "description": sanitize_cloud_mcp_value(item.description),
        "risk_level": sanitize_cloud_mcp_value(item.risk_level),
        "required": bool(item.required),
        "sensitive": bool(item.sensitive),
    }


def cloud_mcp_install_entry_to_dict(item: CloudMCPInstallEntry) -> dict[str, object]:
    return {
        "type": sanitize_cloud_mcp_value(item.type),
        "label": sanitize_cloud_mcp_value(item.label),
        "enabled": bool(item.enabled),
        "reason": sanitize_cloud_mcp_value(item.reason),
        "path": sanitize_cloud_mcp_value(item.path) if item.path is not None else None,
    }


def cloud_mcp_service_catalog_item_to_dict(item: CloudMCPServiceCatalogItem) -> dict[str, object]:
    return {
        "service_id": sanitize_cloud_mcp_value(item.service_id),
        "display_name": sanitize_cloud_mcp_value(item.display_name),
        "description": sanitize_cloud_mcp_value(item.description),
        "category": sanitize_cloud_mcp_value(item.category),
        "version": sanitize_cloud_mcp_value(item.version),
        "status": sanitize_cloud_mcp_value(item.status),
        "enabled": bool(item.enabled),
        "official": bool(item.official),
        "requires_oauth": bool(item.requires_oauth),
        "oauth_provider": sanitize_cloud_mcp_value(item.oauth_provider) if item.oauth_provider is not None else None,
        "permissions": [cloud_mcp_catalog_permission_to_dict(permission) for permission in item.permissions],
        "tags": sanitize_cloud_mcp_value(list(item.tags)),
        "install_entry": cloud_mcp_install_entry_to_dict(item.install_entry),
        "metadata": sanitize_cloud_mcp_value(dict(item.metadata)),
    }


def cloud_mcp_catalog_to_dict(catalog: CloudMCPCatalog) -> dict[str, object]:
    return {
        "enabled": bool(catalog.enabled),
        "version": sanitize_cloud_mcp_value(catalog.version),
        "services": [cloud_mcp_service_catalog_item_to_dict(service) for service in catalog.services],
        "warnings": sanitize_cloud_mcp_value(list(catalog.warnings)),
    }


def cloud_mcp_catalog_lookup_result_to_dict(result: CloudMCPCatalogLookupResult) -> dict[str, object]:
    return {
        "found": bool(result.found),
        "service": cloud_mcp_service_catalog_item_to_dict(result.service) if result.service is not None else None,
        "error": sanitize_cloud_mcp_value(result.error) if result.error is not None else None,
    }
