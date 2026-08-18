"""User-scoped daily rate limiting built on UsageTracker."""

from __future__ import annotations

from typing import Any

from config.settings import settings
from core.usage import UsageTracker


ACTION_LIMITS = {
    "chat": "daily_chat_limit",
    "rag": "daily_rag_limit",
    "web_search": "daily_web_search_limit",
    "embedding": "daily_embedding_limit",
    "document_load": "daily_document_load_limit",
    "sandbox": "daily_sandbox_limit",
    "browser": "daily_browser_limit",
}


class RateLimiter:
    """Check and increment local daily usage quotas."""

    def __init__(self, tracker: UsageTracker | None = None) -> None:
        self.tracker = tracker or UsageTracker()

    def check_and_increment(self, user_id: str, action: str, amount: int = 1) -> dict[str, Any]:
        """Return an allow/deny result and increment when allowed."""

        safe_action = str(action or "").strip()
        limit = self._limit_for(safe_action)
        if not settings.rate_limit_enabled:
            return {"allowed": True, "action": safe_action, "used": 0, "limit": limit, "remaining": limit}
        current = self.tracker.get_usage(user_id).get(safe_action, 0)
        safe_amount = max(1, int(amount or 1))
        allowed = current + safe_amount <= limit
        if not allowed:
            return {
                "allowed": False,
                "action": safe_action,
                "used": current,
                "limit": limit,
                "remaining": max(0, limit - current),
            }
        usage = self.tracker.increment(user_id, safe_action, safe_amount)
        used = int(usage.get(safe_action, 0) or 0)
        return {
            "allowed": True,
            "action": safe_action,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }

    @staticmethod
    def _limit_for(action: str) -> int:
        setting_name = ACTION_LIMITS.get(action)
        if not setting_name:
            raise ValueError(f"unsupported rate limit action: {action}")
        return max(0, int(getattr(settings, setting_name, 0) or 0))
