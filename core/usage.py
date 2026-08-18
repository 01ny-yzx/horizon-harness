"""Local JSON usage accounting for user-scoped rate limits."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.workspace import PROJECT_ROOT, WorkspaceManager


ALLOWED_ACTIONS = {"chat", "rag", "web_search", "embedding", "document_load", "sandbox"}
USAGE_DIR = PROJECT_ROOT / "usage_store"
USAGE_FILE = USAGE_DIR / "usage.json"


class UsageTracker:
    """Track daily local usage counters in a small JSON file."""

    def __init__(self, usage_path: str | Path | None = None) -> None:
        self.usage_path = Path(usage_path) if usage_path is not None else USAGE_FILE
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def increment(self, user_id: str, action: str, amount: int = 1) -> dict[str, Any]:
        """Increment one action counter for today's sanitized user id."""

        safe_user = self._sanitize_user_id(user_id)
        safe_action = self._sanitize_action(action)
        safe_amount = max(0, int(amount or 0))
        payload = self._load()
        records = payload.setdefault("records", {})
        day = records.setdefault(_today(), {})
        user_record = day.setdefault(safe_user, {})
        user_record[safe_action] = int(user_record.get(safe_action, 0) or 0) + safe_amount
        self._save(payload)
        return self.get_usage(safe_user)

    def get_usage(self, user_id: str, date: str | None = None) -> dict[str, int]:
        """Return counters for one user and date."""

        safe_user = self._sanitize_user_id(user_id)
        payload = self._load()
        day = str(date or _today())
        record = payload.get("records", {}).get(day, {}).get(safe_user, {})
        return {action: int(record.get(action, 0) or 0) for action in sorted(ALLOWED_ACTIONS)}

    def check_limit(self, user_id: str, action: str, limit: int) -> dict[str, Any]:
        """Check whether one more unit would fit within the configured limit."""

        safe_action = self._sanitize_action(action)
        used = self.get_usage(user_id).get(safe_action, 0)
        safe_limit = max(0, int(limit or 0))
        return {
            "allowed": used < safe_limit,
            "action": safe_action,
            "used": used,
            "limit": safe_limit,
            "remaining": max(0, safe_limit - used),
        }

    def reset_day(self, date: str | None = None) -> dict[str, Any]:
        """Remove all counters for one day."""

        payload = self._load()
        day = str(date or _today())
        existed = day in payload.get("records", {})
        payload.setdefault("records", {}).pop(day, None)
        self._save(payload)
        return {"success": True, "data": {"date": day, "deleted": existed}}

    def format_summary(self, user_id: str) -> dict[str, Any]:
        """Return a compact today's usage summary."""

        safe_user = self._sanitize_user_id(user_id)
        return {"success": True, "data": {"date": _today(), "user_id": safe_user, "usage": self.get_usage(safe_user)}}

    def _load(self) -> dict[str, Any]:
        self._ensure_file()
        try:
            payload = json.loads(self.usage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._backup_corrupt_file()
            payload = self._empty_payload()
            self._save(payload)
        if not isinstance(payload, dict):
            payload = self._empty_payload()
        payload.setdefault("version", 1)
        payload.setdefault("records", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.usage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ensure_file(self) -> None:
        if not self.usage_path.exists():
            self._save(self._empty_payload())

    def _backup_corrupt_file(self) -> None:
        if self.usage_path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            shutil.copy2(self.usage_path, self.usage_path.with_suffix(f".corrupt-{stamp}.json"))

    @staticmethod
    def _empty_payload() -> dict[str, Any]:
        return {"version": 1, "records": {}}

    @staticmethod
    def _sanitize_user_id(user_id: str) -> str:
        return WorkspaceManager().sanitize_id(user_id, "default_user")

    @staticmethod
    def _sanitize_action(action: str) -> str:
        safe_action = str(action or "").strip()
        if safe_action not in ALLOWED_ACTIONS:
            raise ValueError(f"unsupported usage action: {safe_action}")
        return safe_action


def _today() -> str:
    return datetime.now().date().isoformat()
