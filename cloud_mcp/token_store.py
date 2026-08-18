"""Server-side Cloud MCP token store foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from cloud_mcp.gateway import sanitize_cloud_mcp_value
from cloud_mcp.oauth import (
    OAUTH_CONNECTION_NOT_FOUND,
    OAUTH_PROVIDER_NOT_FOUND,
    CloudMCPOAuthConnectionStatus,
    get_cloud_mcp_oauth_provider,
)


@dataclass(frozen=True)
class CloudMCPTokenRecord:
    provider_id: str
    subject_id: str
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    scopes: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = field(default_factory=dict)


def build_token_key(provider_id: str, subject_id: str) -> str:
    provider = provider_id.strip().lower() if isinstance(provider_id, str) and provider_id.strip() else "unknown_provider"
    subject = subject_id.strip().lower() if isinstance(subject_id, str) and subject_id.strip() else "unknown_subject"
    return f"{provider}:{subject}"


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_token_expired(record: CloudMCPTokenRecord, *, now: datetime | None = None) -> bool:
    if record.expires_at is None:
        return False
    current = _as_aware_utc(now or datetime.now(timezone.utc))
    expires_at = _as_aware_utc(record.expires_at)
    return expires_at <= current


def _datetime_to_public_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_aware_utc(value).isoformat()


def token_record_to_public_dict(record: CloudMCPTokenRecord, *, now: datetime | None = None) -> dict[str, object]:
    return {
        "provider_id": sanitize_cloud_mcp_value(record.provider_id),
        "subject_id": sanitize_cloud_mcp_value(record.subject_id),
        "token_type": sanitize_cloud_mcp_value(record.token_type),
        "scopes": sanitize_cloud_mcp_value(list(record.scopes)),
        "expires_at": _datetime_to_public_text(record.expires_at),
        "created_at": _datetime_to_public_text(record.created_at),
        "updated_at": _datetime_to_public_text(record.updated_at),
        "expired": is_token_expired(record, now=now),
        "token_available": True,
        "token_visible": False,
        "metadata": sanitize_cloud_mcp_value(dict(record.metadata)),
    }


class CloudMCPTokenStore:
    def save_token(self, record: CloudMCPTokenRecord) -> None:
        raise NotImplementedError

    def get_token(self, provider_id: str, subject_id: str) -> CloudMCPTokenRecord | None:
        raise NotImplementedError

    def delete_token(self, provider_id: str, subject_id: str) -> bool:
        raise NotImplementedError

    def list_tokens(self, *, provider_id: str | None = None) -> list[CloudMCPTokenRecord]:
        raise NotImplementedError


class InMemoryCloudMCPTokenStore(CloudMCPTokenStore):
    def __init__(self, initial_records: list[CloudMCPTokenRecord] | None = None) -> None:
        self._records: dict[str, CloudMCPTokenRecord] = {}
        for record in initial_records or []:
            self.save_token(record)

    def save_token(self, record: CloudMCPTokenRecord) -> None:
        self._records[build_token_key(record.provider_id, record.subject_id)] = record

    def get_token(self, provider_id: str, subject_id: str) -> CloudMCPTokenRecord | None:
        return self._records.get(build_token_key(provider_id, subject_id))

    def delete_token(self, provider_id: str, subject_id: str) -> bool:
        key = build_token_key(provider_id, subject_id)
        if key not in self._records:
            return False
        del self._records[key]
        return True

    def list_tokens(self, *, provider_id: str | None = None) -> list[CloudMCPTokenRecord]:
        records = list(self._records.values())
        if provider_id:
            expected = provider_id.strip().lower()
            records = [record for record in records if record.provider_id.strip().lower() == expected]
        return records


def create_in_memory_token_store(
    initial_records: list[CloudMCPTokenRecord] | None = None,
) -> InMemoryCloudMCPTokenStore:
    return InMemoryCloudMCPTokenStore(initial_records=initial_records)


def build_oauth_connection_status(
    provider_id: str,
    *,
    subject_id: str = "default",
    token_store: CloudMCPTokenStore | None = None,
    now: datetime | None = None,
) -> CloudMCPOAuthConnectionStatus:
    provider = get_cloud_mcp_oauth_provider(provider_id)
    normalized_provider = provider_id.strip().lower() if isinstance(provider_id, str) and provider_id.strip() else ""
    if provider is None:
        return CloudMCPOAuthConnectionStatus(provider_id=normalized_provider, connected=False, reason=OAUTH_PROVIDER_NOT_FOUND, token_visible=False)
    if token_store is None:
        return CloudMCPOAuthConnectionStatus(provider_id=provider.provider_id, connected=False, reason=OAUTH_CONNECTION_NOT_FOUND, token_visible=False)
    record = token_store.get_token(provider.provider_id, subject_id)
    if record is None:
        return CloudMCPOAuthConnectionStatus(provider_id=provider.provider_id, connected=False, reason=OAUTH_CONNECTION_NOT_FOUND, token_visible=False)
    expired = is_token_expired(record, now=now)
    return CloudMCPOAuthConnectionStatus(
        provider_id=provider.provider_id,
        connected=not expired,
        token_available=True,
        expired=expired,
        scopes=list(record.scopes),
        expires_at=_datetime_to_public_text(record.expires_at),
        token_visible=False,
        reason=None if not expired else "cloud_mcp_oauth_token_expired",
        metadata={"token_store": "server_side"},
    )
