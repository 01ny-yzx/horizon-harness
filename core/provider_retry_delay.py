"""Deterministic bounded retry-delay handling for Provider failures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping


DEFAULT_PROVIDER_RETRY_DELAY_MS = 2000
MAX_INLINE_PROVIDER_RETRY_DELAY_MS = 10000


@dataclass(frozen=True)
class ProviderRetryDelayDecision:
    requested_delay_ms: int
    applied_delay_ms: int
    source: str
    should_retry_inline: bool
    reason: str


def compute_provider_retry_delay(
    response_headers: Mapping[str, str] | None,
    *,
    now: datetime | None = None,
    default_delay_ms: int = DEFAULT_PROVIDER_RETRY_DELAY_MS,
    max_inline_delay_ms: int = MAX_INLINE_PROVIDER_RETRY_DELAY_MS,
) -> ProviderRetryDelayDecision:
    """Choose bounded synchronous retry timing from safe retry headers."""

    headers = {
        str(key).lower(): str(value)
        for key, value in dict(response_headers or {}).items()
    }
    requested, source = _requested_delay(headers, now=now)
    if requested is None:
        requested = max(0, int(default_delay_ms))
        source = "default-backoff"
    if requested <= max(0, int(max_inline_delay_ms)):
        return ProviderRetryDelayDecision(
            requested_delay_ms=requested,
            applied_delay_ms=requested,
            source=source,
            should_retry_inline=True,
            reason="provider_retry_delay_applied",
        )
    return ProviderRetryDelayDecision(
        requested_delay_ms=requested,
        applied_delay_ms=0,
        source=source,
        should_retry_inline=False,
        reason="provider_retry_delay_exceeds_inline_limit",
    )


def _requested_delay(
    headers: Mapping[str, str],
    *,
    now: datetime | None,
) -> tuple[int | None, str]:
    milliseconds = _nonnegative_number(headers.get("retry-after-ms"))
    if milliseconds is not None:
        return math.ceil(milliseconds), "retry-after-ms"
    retry_after = str(headers.get("retry-after") or "").strip()
    seconds = _nonnegative_number(retry_after)
    if seconds is not None:
        return math.ceil(seconds * 1000), "retry-after-seconds"
    if retry_after:
        try:
            target = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None, ""
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        delay_ms = math.ceil((target - current).total_seconds() * 1000)
        if delay_ms >= 0:
            return delay_ms, "retry-after-date"
    return None, ""


def _nonnegative_number(value: str | None) -> float | None:
    try:
        parsed = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed
