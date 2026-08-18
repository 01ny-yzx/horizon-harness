"""Cloud MCP OAuth provider metadata foundation."""

from __future__ import annotations

from dataclasses import dataclass, field

from cloud_mcp.gateway import sanitize_cloud_mcp_value


OAUTH_PROVIDER_NOT_FOUND = "cloud_mcp_oauth_provider_not_found"
OAUTH_NOT_READY = "cloud_mcp_oauth_not_ready"
OAUTH_CONNECTION_NOT_FOUND = "cloud_mcp_oauth_connection_not_found"
OAUTH_CONNECTION_REQUIRED = "cloud_mcp_oauth_connection_required"


@dataclass(frozen=True)
class CloudMCPOAuthProvider:
    provider_id: str
    display_name: str
    auth_type: str = "oauth2"
    enabled: bool = False
    authorization_url: str | None = None
    token_url: str | None = None
    scopes: list[str] = field(default_factory=list)
    supports_refresh: bool = True
    status: str = "foundation"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudMCPOAuthConnectionStatus:
    provider_id: str
    connected: bool
    token_available: bool = False
    expired: bool = False
    scopes: list[str] = field(default_factory=list)
    expires_at: str | None = None
    token_visible: bool = False
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def _providers_enabled_from_settings() -> bool:
    try:
        from config.settings import settings

        return bool(getattr(settings, "cloud_mcp_oauth_providers_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def default_cloud_mcp_oauth_providers() -> list[CloudMCPOAuthProvider]:
    providers_enabled = _providers_enabled_from_settings()
    return [
        CloudMCPOAuthProvider(
            provider_id="github",
            display_name="GitHub",
            enabled=providers_enabled,
            authorization_url="https://github.com/login/oauth/authorize",
            token_url=None,
            scopes=["repo:read", "issues:write"],
            metadata={"foundation": "27.3", "flow": "not_connected"},
        ),
        CloudMCPOAuthProvider(
            provider_id="google",
            display_name="Google",
            enabled=False,
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url=None,
            scopes=["gmail.readonly", "drive.metadata.readonly"],
            metadata={"foundation": "27.3", "flow": "not_connected"},
        ),
    ]


def list_cloud_mcp_oauth_providers(
    *,
    include_disabled: bool = True,
) -> list[CloudMCPOAuthProvider]:
    try:
        providers = default_cloud_mcp_oauth_providers()
        if not include_disabled:
            providers = [provider for provider in providers if provider.enabled]
        return providers
    except Exception:  # noqa: BLE001
        return []


def get_cloud_mcp_oauth_provider(provider_id: str) -> CloudMCPOAuthProvider | None:
    try:
        if not isinstance(provider_id, str) or not provider_id.strip():
            return None
        expected = provider_id.strip().lower()
        for provider in default_cloud_mcp_oauth_providers():
            if provider.provider_id.lower() == expected:
                return provider
        return None
    except Exception:  # noqa: BLE001
        return None


def cloud_mcp_oauth_provider_to_dict(provider: CloudMCPOAuthProvider) -> dict[str, object]:
    return {
        "provider_id": sanitize_cloud_mcp_value(provider.provider_id),
        "display_name": sanitize_cloud_mcp_value(provider.display_name),
        "auth_type": sanitize_cloud_mcp_value(provider.auth_type),
        "enabled": bool(provider.enabled),
        "authorization_url": sanitize_cloud_mcp_value(provider.authorization_url) if provider.authorization_url is not None else None,
        "token_url": None,
        "scopes": sanitize_cloud_mcp_value(list(provider.scopes)),
        "supports_refresh": bool(provider.supports_refresh),
        "status": sanitize_cloud_mcp_value(provider.status),
        "metadata": sanitize_cloud_mcp_value(dict(provider.metadata)),
    }


def cloud_mcp_oauth_connection_status_to_dict(status: CloudMCPOAuthConnectionStatus) -> dict[str, object]:
    return {
        "provider_id": sanitize_cloud_mcp_value(status.provider_id),
        "connected": bool(status.connected),
        "token_available": bool(status.token_available),
        "expired": bool(status.expired),
        "scopes": sanitize_cloud_mcp_value(list(status.scopes)),
        "expires_at": sanitize_cloud_mcp_value(status.expires_at) if status.expires_at is not None else None,
        "token_visible": False,
        "reason": sanitize_cloud_mcp_value(status.reason) if status.reason is not None else None,
        "metadata": sanitize_cloud_mcp_value(dict(status.metadata)),
    }
