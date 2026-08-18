"""Lightweight API key authentication for the local service API."""

from __future__ import annotations

import hmac

from config.settings import settings


def is_auth_enabled() -> bool:
    """Return whether API key checks should be enforced."""

    return bool(settings.api_auth_enabled)


def get_allowed_api_keys() -> tuple[str, ...]:
    """Return configured caller API keys without logging or printing them."""

    return tuple(key for key in settings.api_keys if key)


def validate_api_key(api_key: str | None) -> bool:
    """Validate a caller API key when auth is enabled."""

    if not is_auth_enabled():
        return True
    candidate = str(api_key or "").strip()
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate, allowed) for allowed in get_allowed_api_keys())
