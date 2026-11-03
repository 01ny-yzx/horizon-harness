"""Local usage accounting. Daily request quotas are no longer enforced."""
from __future__ import annotations

from typing import Any
from config.settings import settings  # Retained for callers using the legacy configuration object.
from core.usage import ALLOWED_ACTIONS, UsageTracker


class RateLimiter:
    """Historical entry point retained for usage reporting; always allows requests."""

    def __init__(self, tracker: UsageTracker | None = None) -> None:
        self.tracker = tracker or UsageTracker()

    def check_and_increment(self, user_id: str, action: str, amount: int = 1) -> dict[str, Any]:
        safe_action = str(action or '').strip()
        if safe_action not in ALLOWED_ACTIONS:
            raise ValueError(f'unsupported usage action: {action}')
        usage = self.tracker.increment(user_id, safe_action, max(1, int(amount or 1)))
        return {'allowed': True, 'enabled': False, 'action': safe_action,
                'used': int(usage.get(safe_action, 0)), 'limit': None, 'remaining': None}
