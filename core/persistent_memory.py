"""Small JSON-backed persistent memory store."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = PROJECT_ROOT / "memory_store"
USER_MEMORY_FILE = MEMORY_DIR / "user_memory.json"
PROJECT_MEMORY_FILE = MEMORY_DIR / "project_memory.json"
TASK_HISTORY_FILE = MEMORY_DIR / "task_history.json"
MEMORY_INDEX_FILE = MEMORY_DIR / "memory_index.json"
MAX_TASKS = 100
MAX_CONTENT_CHARS = 800
SECRET_RE = re.compile(r"(?i)(api[_-]?key|password|passwd|token|secret)\s*[:=]|sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,}")
POLLUTED_RESEARCH_SUMMARY_MARKERS = (
    "final answer",
    "```",
    "tool_calls",
    "traceback",
    "建议：",
    "该专题页本次未成功读取",
)
COMPACTED_RESEARCH_SUMMARY = "Research task history entry was compacted to remove unsafe final-answer content."
PREFERENCE_KEYS = {"answer_language", "answer_style", "answer_tone", "user_preference"}
PREFERENCE_HINTS = {"偏好", "风格", "回答风格", "语言", "语气", "中文", "简单", "简洁", "温柔", "温柔中文", "这个偏好", "之前的偏好"}


def utc_now() -> str:
    """Return an ISO UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


class PersistentMemory:
    """A lightweight persistent memory backed by local JSON files."""

    def __init__(self, memory_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir or MEMORY_DIR
        self.user_memory_path = self.memory_dir / "user_memory.json"
        self.project_memory_path = self.memory_dir / "project_memory.json"
        self.task_history_path = self.memory_dir / "task_history.json"
        self.memory_index_path = self.memory_dir / "memory_index.json"
        self.user_memory: dict[str, Any] = {}
        self.project_memory: dict[str, Any] = {}
        self.task_history: dict[str, Any] = {}
        self.memory_index: dict[str, Any] = {}
        self._ensure_store()
        self.load_all()

    def load_all(self) -> dict[str, Any]:
        """Load all memory files, returning a structured status."""

        try:
            self.user_memory = self._read_json(self.user_memory_path, self._default_user_memory())
            self.project_memory = self._read_json(self.project_memory_path, self._default_project_memory())
            self.task_history = self._read_json(self.task_history_path, self._default_task_history())
            self.memory_index = self._read_json(self.memory_index_path, self._default_memory_index())
            self._refresh_index()
            return {"success": True, "data": self.get_counts()}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"load persistent memory failed: {exc}"}

    def save_all(self) -> dict[str, Any]:
        """Persist all memory files."""

        try:
            self._refresh_index()
            self._write_json(self.user_memory_path, self.user_memory)
            self._write_json(self.project_memory_path, self.project_memory)
            self._write_json(self.task_history_path, self.task_history)
            self._write_json(self.memory_index_path, self.memory_index)
            return {"success": True, "data": self.get_counts()}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"save persistent memory failed: {exc}"}

    def get_user_memory(self) -> dict[str, Any]:
        """Return user memory."""

        return self.user_memory

    def get_project_memory(self) -> dict[str, Any]:
        """Return project memory."""

        return self.project_memory

    def get_recent_tasks(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent task summaries."""

        tasks = self.task_history.get("tasks", [])
        if not isinstance(tasks, list):
            return []
        return tasks[-max(1, limit) :]

    def safe_task_prompt_summary(self, task: dict[str, Any]) -> str:
        """Return a prompt-safe task summary that never exposes full research answers."""

        return self._task_prompt_summary(task)

    def get_prompt_safe_recent_tasks(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent task entries with prompt-safe summaries."""

        safe_tasks: list[dict[str, Any]] = []
        for task in self.get_recent_tasks(limit=limit):
            if not isinstance(task, dict) or task.get("hidden_from_memory_prompt") is True:
                continue
            item = dict(task)
            item["summary"] = self.safe_task_prompt_summary(task)
            safe_tasks.append(item)
        return safe_tasks

    def add_user_preference(self, key: str, value: str, source: str = "user") -> dict[str, Any]:
        """Add or update a user preference."""

        if self._is_sensitive(f"{key} {value}"):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        key = self._clean_text(key, 120)
        value = self._clean_text(value, MAX_CONTENT_CHARS)
        if not key or not value:
            return {"success": False, "error": "Preference key and value are required."}
        now = utc_now()
        preferences = self.user_memory.setdefault("preferences", {})
        existing = preferences.get(key)
        created_at = existing.get("created_at", now) if isinstance(existing, dict) else now
        preferences[key] = {
            "value": value,
            "created_at": created_at,
            "updated_at": now,
            "source": self._clean_text(source, 80),
        }
        self.user_memory["updated_at"] = now
        return self.save_all()

    def add_stable_fact(self, content: str, source: str = "user") -> dict[str, Any]:
        """Add a stable fact if it is not already stored."""

        if self._is_sensitive(content):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        content = self._clean_text(content, MAX_CONTENT_CHARS)
        if not content:
            return {"success": False, "error": "Stable fact content is required."}
        facts = self.user_memory.setdefault("stable_facts", [])
        if any(item.get("content") == content for item in facts if isinstance(item, dict)):
            return {"success": True, "data": {"deduplicated": True}}
        now = utc_now()
        facts.append({"content": content, "created_at": now, "updated_at": now, "source": self._clean_text(source, 80)})
        self.user_memory["updated_at"] = now
        return self.save_all()

    def add_project_summary(
        self,
        project_path: str,
        summary: str,
        tech_stack: list[str] | None = None,
        status: str | None = None,
        source: str = "user",
    ) -> dict[str, Any]:
        """Add or update a project summary."""

        payload = f"{project_path} {summary} {tech_stack} {status}"
        if self._is_sensitive(payload):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        project_path = self._clean_text(project_path, 300)
        summary = self._clean_text(summary, MAX_CONTENT_CHARS)
        if not summary:
            return {"success": False, "error": "Project summary is required."}
        projects = self.project_memory.setdefault("projects", [])
        now = utc_now()
        for project in projects:
            if isinstance(project, dict) and project.get("project_path") == project_path:
                project.update(
                    {
                        "summary": summary,
                        "tech_stack": tech_stack or project.get("tech_stack") or [],
                        "status": self._clean_text(status or project.get("status") or "", 120),
                        "updated_at": now,
                        "source": self._clean_text(source, 80),
                    }
                )
                return self.save_all()
        projects.append(
            {
                "project_path": project_path,
                "summary": summary,
                "tech_stack": tech_stack or [],
                "status": self._clean_text(status or "", 120),
                "created_at": now,
                "updated_at": now,
                "source": self._clean_text(source, 80),
            }
        )
        return self.save_all()

    def add_task_summary(self, task_summary: dict[str, Any]) -> dict[str, Any]:
        """Append a compact task summary, keeping only the latest 100."""

        cleaned = self._clean_task_summary(task_summary)
        if self._is_sensitive(json.dumps(cleaned, ensure_ascii=False)):
            return {"success": False, "error": "Refusing to store sensitive memory."}
        tasks = self.task_history.setdefault("tasks", [])
        task_id = cleaned.get("task_id")
        if task_id and any(item.get("task_id") == task_id for item in tasks if isinstance(item, dict)):
            return {"success": True, "data": {"deduplicated": True}}
        tasks.append(cleaned)
        self.task_history["tasks"] = tasks[-MAX_TASKS:]
        return self.save_all()

    def forget_memory(self, memory_type: str, keyword: str) -> dict[str, Any]:
        """Delete memory entries matching a keyword."""

        normalized_type = (memory_type or "all").lower()
        keyword = self._clean_text(keyword, 120)
        if not keyword:
            return {"success": False, "error": "keyword is required."}

        deleted_preferences: list[str] = []
        deleted_stable_facts = 0
        deleted_projects = 0
        deleted_tasks = 0
        deleted_preference_values: list[str] = []

        if normalized_type in {"all", "user", "user_preference"}:
            deleted_preferences, deleted_preference_values = self._forget_preferences(keyword)
        if normalized_type in {"all", "user", "stable_fact"}:
            deleted_stable_facts = self._forget_stable_facts(keyword)
        if normalized_type in {"all", "project", "project_summary"}:
            deleted_projects = self._forget_project(keyword)
        if normalized_type in {"all", "tasks", "task"}:
            deleted_tasks += self._forget_tasks(keyword)

        if deleted_preferences:
            deleted_tasks += self._purge_preference_task_history(keyword, deleted_preferences, deleted_preference_values)

        save_result = self.save_all()
        if not save_result.get("success"):
            return save_result
        self.load_all()

        remaining_preferences = sorted((self.user_memory.get("preferences") or {}).keys())
        deleted = len(deleted_preferences) + deleted_stable_facts + deleted_projects + deleted_tasks
        data = {
            "deleted": deleted,
            "deleted_preferences": deleted_preferences,
            "deleted_stable_facts": deleted_stable_facts,
            "deleted_projects": deleted_projects,
            "deleted_tasks": deleted_tasks,
            "remaining_preferences": remaining_preferences,
        }
        if deleted == 0:
            data["message"] = "没有找到匹配的长期记忆"
        return {"success": True, "data": data}

    def clear_memory_type(self, memory_type: str) -> dict[str, Any]:
        """Clear one allowed memory category without deleting memory_store."""

        normalized = (memory_type or "").lower()
        if normalized == "user_preferences":
            count = len(self.user_memory.get("preferences", {}) if isinstance(self.user_memory.get("preferences"), dict) else {})
            self.user_memory["preferences"] = {}
        elif normalized == "stable_facts":
            count = len(self.user_memory.get("stable_facts", []) if isinstance(self.user_memory.get("stable_facts"), list) else [])
            self.user_memory["stable_facts"] = []
        elif normalized == "task_history":
            count = len(self.task_history.get("tasks", []) if isinstance(self.task_history.get("tasks"), list) else [])
            self.task_history["tasks"] = []
        elif normalized == "project_memory":
            count = len(self.project_memory.get("projects", []) if isinstance(self.project_memory.get("projects"), list) else [])
            self.project_memory["projects"] = []
        else:
            return {"success": False, "error": "memory_type must be one of: user_preferences, stable_facts, task_history, project_memory."}
        result = self.save_all()
        if result.get("success"):
            result["data"] = {"cleared": count, "memory_type": normalized}
        return result

    def format_for_prompt(
        self,
        max_chars: int = 2000,
        include_task_history: bool = True,
        include_preferences: bool = True,
        include_project_memory: bool = True,
        mode: str = "default",
    ) -> str:
        """Format long-term memory as concise LLM context."""

        lines = ["Long-term memory (use as helpful context, not absolute truth):"]
        preferences = self.user_memory.get("preferences", {})
        if include_preferences and isinstance(preferences, dict) and preferences:
            lines.append("User preferences:")
            for key, item in list(preferences.items())[:8]:
                value = item.get("value") if isinstance(item, dict) else item
                if mode == "rag" and str(key) not in {"answer_language", "answer_style", "answer_tone", "user_preference"}:
                    continue
                lines.append(f"- {key}: {value}")
        facts = self.user_memory.get("stable_facts", [])
        if isinstance(facts, list) and facts:
            lines.append("Stable facts:")
            for item in facts[-5 if mode == "rag" else -8:]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('content')}")
        projects = self.project_memory.get("projects", [])
        if include_project_memory and isinstance(projects, list) and projects:
            lines.append("Projects:")
            for project in projects[-3 if mode == "rag" else -5:]:
                if isinstance(project, dict):
                    stack = ", ".join(project.get("tech_stack") or [])
                    summary = str(project.get("summary", ""))[:300 if mode == "rag" else MAX_CONTENT_CHARS]
                    lines.append(f"- {project.get('project_path') or 'current project'}: {summary} {stack}".strip())
        task_limit = 3 if mode == "rag" else 5
        recent_tasks = self.get_prompt_safe_recent_tasks(limit=12)[:task_limit]
        if not include_task_history:
            recent_tasks = []
        if recent_tasks:
            lines.append("Recent tasks:")
            for task in recent_tasks:
                text = self._task_prompt_summary(task)
                lines.append(f"- [{task.get('task_type')}] {text} ({task.get('result')})")
        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[:max_chars] + "\n... [long-term memory truncated]"
        return text

    def get_counts(self) -> dict[str, int]:
        """Return memory counts."""

        preferences = self.user_memory.get("preferences", {})
        facts = self.user_memory.get("stable_facts", [])
        projects = self.project_memory.get("projects", [])
        tasks = self.task_history.get("tasks", [])
        return {
            "user_memories": len(preferences if isinstance(preferences, dict) else {}) + len(facts if isinstance(facts, list) else []),
            "projects": len(projects if isinstance(projects, list) else []),
            "tasks": len(tasks if isinstance(tasks, list) else []),
        }

    def _ensure_store(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        defaults = {
            self.user_memory_path: self._default_user_memory(),
            self.project_memory_path: self._default_project_memory(),
            self.task_history_path: self._default_task_history(),
            self.memory_index_path: self._default_memory_index(),
        }
        for path, data in defaults.items():
            if not path.exists():
                self._write_json(path, data)

    def _refresh_index(self) -> None:
        self.memory_index = {
            "version": 1,
            "last_updated": utc_now(),
            "counts": self.get_counts(),
        }

    @staticmethod
    def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _default_user_memory() -> dict[str, Any]:
        return {"preferences": {}, "stable_facts": [], "updated_at": ""}

    @staticmethod
    def _default_project_memory() -> dict[str, Any]:
        return {"projects": []}

    @staticmethod
    def _default_task_history() -> dict[str, Any]:
        return {"tasks": []}

    @staticmethod
    def _default_memory_index() -> dict[str, Any]:
        return {"version": 1, "last_updated": "", "counts": {"user_memories": 0, "projects": 0, "tasks": 0}}

    @staticmethod
    def _clean_text(text: Any, limit: int) -> str:
        return " ".join(str(text or "").split())[:limit]

    @staticmethod
    def _is_sensitive(text: str) -> bool:
        return bool(SECRET_RE.search(text or ""))

    def _clean_task_summary(self, task_summary: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        task_type = self._clean_text(task_summary.get("task_type"), 40)
        summary = self._clean_text(task_summary.get("summary"), MAX_CONTENT_CHARS)
        if task_type == "research" and self._should_compact_research_summary(summary):
            summary = COMPACTED_RESEARCH_SUMMARY
        return {
            "task_id": self._clean_text(task_summary.get("task_id"), 80),
            "task_type": task_type,
            "goal": self._clean_text(task_summary.get("goal"), 300),
            "result": self._clean_text(task_summary.get("result"), 40),
            "summary": summary,
            "modified_files": [self._clean_text(item, 260) for item in task_summary.get("modified_files", [])[:20]],
            "hidden_from_memory_prompt": (
                task_summary.get("hidden_from_memory_prompt") is True
            ),
            "created_at": self._clean_text(task_summary.get("created_at") or now, 80),
            "updated_at": now,
            "source": self._clean_text(task_summary.get("source") or "agent", 80),
        }

    def _task_prompt_summary(self, task: dict[str, Any]) -> str:
        summary = self._clean_text(task.get("summary") or task.get("goal"), 300)
        if self._should_compact_research_summary(summary):
            return COMPACTED_RESEARCH_SUMMARY
        return summary

    def _should_compact_research_summary(self, summary: str) -> bool:
        text = str(summary or "")
        return self._has_polluted_research_content(text) or _url_count(text) > 1

    @staticmethod
    def _has_polluted_research_content(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(marker.lower() in lowered for marker in POLLUTED_RESEARCH_SUMMARY_MARKERS)

    def _forget_preferences(self, keyword: str) -> tuple[list[str], list[str]]:
        preferences = self.user_memory.get("preferences", {})
        if not isinstance(preferences, dict) or not preferences:
            return [], []

        keyword_tokens = _tokens(keyword)
        broad_preference_request = _has_preference_hint(keyword)
        delete_all_single = broad_preference_request and len(preferences) == 1
        deleted_keys: list[str] = []
        deleted_values: list[str] = []
        for key in list(preferences.keys()):
            item = preferences.get(key)
            value = str(item.get("value", "")) if isinstance(item, dict) else str(item)
            if delete_all_single or self._preference_matches(key, value, keyword, keyword_tokens, broad_preference_request):
                deleted_keys.append(key)
                deleted_values.append(value)
                preferences.pop(key, None)
        if deleted_keys:
            self.user_memory["updated_at"] = utc_now()
        return deleted_keys, deleted_values

    def _preference_matches(
        self,
        key: str,
        value: str,
        keyword: str,
        keyword_tokens: set[str],
        broad_preference_request: bool,
    ) -> bool:
        key_lower = key.lower()
        haystack = f"{key_lower} {value.lower()}"
        if keyword.lower() in haystack:
            return True
        if key in PREFERENCE_KEYS and broad_preference_request:
            return True
        if key == "answer_language" and keyword_tokens & {"语言", "中文", "简单", "简洁", "温柔"}:
            return True
        if key == "answer_style" and keyword_tokens & {"风格", "回答风格", "简单", "简洁", "温柔"}:
            return True
        if key == "answer_tone" and keyword_tokens & {"语气", "温柔", "风格"}:
            return True
        value_tokens = _tokens(value)
        return bool(keyword_tokens and keyword_tokens.issubset(value_tokens))

    def _forget_stable_facts(self, keyword: str) -> int:
        facts = self.user_memory.get("stable_facts", [])
        if not isinstance(facts, list):
            return 0
        kept = [item for item in facts if not _text_matches(json.dumps(item, ensure_ascii=False), keyword)]
        self.user_memory["stable_facts"] = kept
        return len(facts) - len(kept)

    def _forget_project(self, keyword: str) -> int:
        projects = self.project_memory.get("projects", [])
        if not isinstance(projects, list):
            return 0
        kept = [item for item in projects if not _text_matches(json.dumps(item, ensure_ascii=False), keyword)]
        self.project_memory["projects"] = kept
        return len(projects) - len(kept)

    def _forget_tasks(self, keyword: str) -> int:
        tasks = self.task_history.get("tasks", [])
        if not isinstance(tasks, list):
            return 0
        kept = [item for item in tasks if not _text_matches(json.dumps(item, ensure_ascii=False), keyword)]
        self.task_history["tasks"] = kept
        return len(tasks) - len(kept)

    def _purge_preference_task_history(
        self,
        keyword: str,
        deleted_keys: list[str],
        deleted_values: list[str],
    ) -> int:
        tasks = self.task_history.get("tasks", [])
        if not isinstance(tasks, list):
            return 0
        markers = {
            keyword,
            *deleted_keys,
            *deleted_values,
            "回答风格偏好",
            "温柔的中文解释",
            "语言简单一点",
            "尽量用温柔的中文解释",
        }
        kept = []
        deleted = 0
        for task in tasks:
            text = json.dumps(task, ensure_ascii=False)
            if any(marker and _text_matches(text, marker) for marker in markers):
                deleted += 1
            else:
                kept.append(task)
        self.task_history["tasks"] = kept
        return deleted

def _has_preference_hint(text: str) -> bool:
    return any(hint in text for hint in PREFERENCE_HINTS)


def _tokens(text: str) -> set[str]:
    tokens = {hint for hint in PREFERENCE_HINTS if hint in text}
    for piece in re.split(r"[\s,，。:：/]+", text):
        if piece:
            tokens.add(piece.lower())
    return tokens


def _text_matches(text: str, keyword: str) -> bool:
    lowered_text = text.lower()
    lowered_keyword = keyword.lower()
    if lowered_keyword and lowered_keyword in lowered_text:
        return True
    keyword_tokens = _tokens(keyword)
    return bool(keyword_tokens and keyword_tokens.issubset(_tokens(text)))


def _url_count(text: str) -> int:
    return len(re.findall(r"https?://\S+", str(text or "")))
