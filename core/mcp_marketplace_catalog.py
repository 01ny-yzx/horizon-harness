"""MCP marketplace catalog loading and install payload generation."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from core.mcp_install_manager import ANY_PLACEHOLDER_PATTERN, PLACEHOLDER_PATTERN, SENSITIVE_KEY_PATTERN, SERVER_NAME_PATTERN


DEFAULT_CATALOG_DIR = Path("marketplace/mcp_catalog")
VALID_STATUSES = {"available", "coming_soon", "disabled"}
REAL_SECRET_HINT = re.compile(r"(ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|sk-[A-Za-z0-9_-]{12,})")


class MCPMarketplaceCatalog:
    """Read-only marketplace catalog for installable MCP server templates."""

    def __init__(self, catalog_dir: str | Path = DEFAULT_CATALOG_DIR) -> None:
        self.catalog_dir = Path(catalog_dir)
        self.items_by_id: dict[str, dict[str, Any]] = {}
        self.items_by_server_name: dict[str, dict[str, Any]] = {}
        self.errors: list[dict[str, Any]] = []
        self.loaded = False

    def load_catalog(self) -> dict[str, Any]:
        self.items_by_id = {}
        self.items_by_server_name = {}
        self.errors = []
        self.loaded = True

        if not self.catalog_dir.exists():
            self.errors.append({"code": "catalog_dir_not_found", "path": str(self.catalog_dir)})
            return {"items": [], "errors": list(self.errors)}
        if not self.catalog_dir.is_dir():
            self.errors.append({"code": "catalog_dir_invalid", "path": str(self.catalog_dir)})
            return {"items": [], "errors": list(self.errors)}

        for path in sorted(self.catalog_dir.glob("*.json"), key=lambda item: item.name):
            raw, error = self._read_json(path)
            if error:
                self.errors.append(error)
                continue
            validation = self.validate_item(raw, source_path=path)
            if not validation["valid"]:
                self.errors.extend(validation["errors"])
                continue
            item = copy.deepcopy(raw)
            item["_source_path"] = str(path)
            item_id = str(item["id"])
            server_name = str(item["server_name"])
            if item_id in self.items_by_id:
                self.errors.append({"code": "duplicate_item_id", "id": item_id, "path": str(path)})
                continue
            if server_name in self.items_by_server_name:
                self.errors.append({"code": "duplicate_server_name", "server_name": server_name, "path": str(path)})
                continue
            self.items_by_id[item_id] = item
            self.items_by_server_name[server_name] = item

        return {"items": self.list_items(), "errors": list(self.errors)}

    def list_items(self) -> list[dict[str, Any]]:
        self._ensure_loaded()
        return [self._summary(item) for item in self.items_by_id.values()]

    def get_item(self, item_id: str) -> dict[str, Any]:
        self._ensure_loaded()
        item = self.items_by_id.get(str(item_id))
        if not item:
            return {"success": False, "status": "not_found", "item_id": item_id, "error_code": "catalog_item_not_found"}
        return {"success": True, "status": "found", "item": self._safe_detail(item)}

    def get_item_by_server_name(self, server_name: str) -> dict[str, Any]:
        self._ensure_loaded()
        item = self.items_by_server_name.get(str(server_name))
        if not item:
            return {
                "success": False,
                "status": "not_found",
                "server_name": server_name,
                "error_code": "catalog_item_not_found",
            }
        return {"success": True, "status": "found", "item": self._safe_detail(item)}

    def list_available(self) -> list[dict[str, Any]]:
        self._ensure_loaded()
        return [self._summary(item) for item in self.items_by_id.values() if item.get("status") == "available"]

    def list_by_category(self, category: str) -> list[dict[str, Any]]:
        self._ensure_loaded()
        return [self._summary(item) for item in self.items_by_id.values() if item.get("category") == category]

    def validate_item(self, data: dict[str, Any], source_path: str | Path | None = None) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        path = str(source_path or "")
        if not isinstance(data, dict):
            return {"valid": False, "errors": [{"code": "catalog_item_invalid_structure", "path": path}]}

        for field in ["id", "server_name", "display_name", "description", "category", "status"]:
            if not str(data.get(field, "")).strip():
                errors.append({"code": "catalog_missing_required_field", "field": field, "path": path})

        server_name = str(data.get("server_name", ""))
        if server_name and not SERVER_NAME_PATTERN.fullmatch(server_name):
            errors.append({"code": "invalid_server_name", "server_name": server_name, "path": path})

        status = str(data.get("status", ""))
        if status and status not in VALID_STATUSES:
            errors.append({"code": "catalog_invalid_status", "status": status, "path": path})

        if "tags" in data and not isinstance(data.get("tags"), list):
            errors.append({"code": "catalog_invalid_tags", "path": path})
        if "env" in data and not isinstance(data.get("env"), list):
            errors.append({"code": "catalog_invalid_env", "path": path})
        if "install_template" in data and not isinstance(data.get("install_template"), dict):
            errors.append({"code": "catalog_invalid_install_template", "path": path})

        if status == "available" and not isinstance(data.get("install_template"), dict):
            errors.append({"code": "catalog_install_template_required", "id": data.get("id"), "path": path})

        errors.extend(self._validate_env_entries(data.get("env", []), path))
        if isinstance(data.get("install_template"), dict):
            errors.extend(self._validate_install_template(data["install_template"], path))
        if str(data.get("transport", "stdio")).lower() in {"http", "remote_http"}:
            template = data.get("install_template") if isinstance(data.get("install_template"), dict) else {}
            if status == "available" and not str(template.get("url", "")).strip():
                errors.append({"code": "catalog_remote_url_required", "path": path})

        return {"valid": not errors, "errors": errors}

    def build_install_payload(self, item_id: str, *, enabled: bool = True, permissions: list[str] | None = None) -> dict[str, Any]:
        self._ensure_loaded()
        item = self.items_by_id.get(str(item_id))
        if not item:
            return {"success": False, "status": "not_found", "item_id": item_id, "error_code": "catalog_item_not_found"}
        if item.get("status") != "available":
            return {
                "success": False,
                "status": "not_installable",
                "item_id": item_id,
                "error_code": "catalog_item_not_installable",
            }
        template = item.get("install_template") or {}
        default_permissions = (item.get("permissions") or {}).get("default", ["read_only"])
        metadata = dict(template.get("metadata") or {})
        metadata.setdefault("marketplace_item_id", item["id"])
        metadata.setdefault("marketplace_version", item.get("version", ""))
        return {
            "success": True,
            "status": "ready",
            "payload": {
                "server_name": item["server_name"],
                "command": str(template.get("command", "")),
                "args": [str(arg) for arg in template.get("args", [])],
                "env": {str(key): str(value) for key, value in (template.get("env") or {}).items()},
                "url": str(template.get("url", "")),
                "headers": {str(key): str(value) for key, value in (template.get("headers") or {}).items()},
                "permissions": [str(value) for value in (permissions or default_permissions)],
                "metadata": metadata,
                "transport": str(item.get("transport", "stdio")),
                "enabled": bool(enabled),
                **({"timeout_seconds": float(template["timeout_seconds"])} if "timeout_seconds" in template else {}),
            },
        }

    def _ensure_loaded(self) -> None:
        if not self.loaded:
            self.load_catalog()

    def _read_json(self, path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}, {"code": "catalog_json_invalid", "path": str(path)}
        except OSError as exc:
            return {}, {"code": "catalog_read_failed", "path": str(path), "error": str(exc)[:300]}
        if not isinstance(raw, dict):
            return {}, {"code": "catalog_item_invalid_structure", "path": str(path)}
        return raw, None

    def _validate_env_entries(self, env_entries: Any, path: str) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if env_entries is None:
            return errors
        if not isinstance(env_entries, list):
            return [{"code": "catalog_invalid_env", "path": path}]
        for entry in env_entries:
            if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
                errors.append({"code": "catalog_invalid_env_entry", "path": path})
                continue
            for field in ["placeholder", "default"]:
                if field in entry:
                    errors.extend(self._validate_env_value(str(entry["name"]), entry[field], path))
            if entry.get("sensitive") is True:
                value = str(entry.get("default", entry.get("placeholder", "")))
                if value and not PLACEHOLDER_PATTERN.fullmatch(value):
                    errors.append({"code": "catalog_sensitive_env_value_invalid", "env": entry["name"], "path": path})
        return errors

    def _validate_install_template(self, template: dict[str, Any], path: str) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        env = template.get("env", {})
        if env is not None and not isinstance(env, dict):
            return [{"code": "catalog_invalid_install_env", "path": path}]
        for key, value in (env or {}).items():
            errors.extend(self._validate_env_value(str(key), value, path))
        url = str(template.get("url", "")).strip()
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append({"code": "catalog_invalid_remote_url", "path": path})
        headers = template.get("headers", {})
        if headers is not None and not isinstance(headers, dict):
            errors.append({"code": "catalog_invalid_install_headers", "path": path})
        elif isinstance(headers, dict):
            for key, value in (headers or {}).items():
                errors.extend(self._validate_header_value(str(key), value, path))
        return errors

    def _validate_env_value(self, key: str, value: Any, path: str) -> list[dict[str, Any]]:
        text = str(value)
        if SENSITIVE_KEY_PATTERN.search(key) and text and not PLACEHOLDER_PATTERN.fullmatch(text):
            return [{"code": "catalog_secret_value_rejected", "env": key, "path": path}]
        if REAL_SECRET_HINT.search(text):
            return [{"code": "catalog_secret_value_rejected", "env": key, "path": path}]
        return []

    def _validate_header_value(self, key: str, value: Any, path: str) -> list[dict[str, Any]]:
        text = str(value)
        if (str(key).lower() == "authorization" or SENSITIVE_KEY_PATTERN.search(key) or REAL_SECRET_HINT.search(text)) and text and not ANY_PLACEHOLDER_PATTERN.search(text):
            return [{"code": "catalog_secret_value_rejected", "header": key, "path": path}]
        return []

    def _summary(self, item: dict[str, Any]) -> dict[str, Any]:
        env_entries = item.get("env") if isinstance(item.get("env"), list) else []
        headers = (item.get("install_template") or {}).get("headers", {}) if isinstance(item.get("install_template"), dict) else {}
        env_keys = [str(entry.get("name")) for entry in env_entries if isinstance(entry, dict) and entry.get("name")]
        sensitive_env_keys = [
            str(entry.get("name"))
            for entry in env_entries
            if isinstance(entry, dict) and entry.get("name") and entry.get("sensitive") is True
        ]
        return {
            "id": item.get("id", ""),
            "server_name": item.get("server_name", ""),
            "display_name": item.get("display_name", ""),
            "description": item.get("description", ""),
            "category": item.get("category", ""),
            "status": item.get("status", ""),
            "official": bool(item.get("official", False)),
            "version": item.get("version", ""),
            "tags": list(item.get("tags") or []),
            "permissions": copy.deepcopy(item.get("permissions") or {}),
            "runtime": copy.deepcopy(item.get("runtime") or {}),
            "resource_scope": copy.deepcopy(item.get("resource_scope") or {}),
            "transport": item.get("transport", "stdio"),
            "url_preview": _url_preview(str((item.get("install_template") or {}).get("url", ""))) if isinstance(item.get("install_template"), dict) else "",
            "header_keys": sorted(str(key) for key in headers.keys()) if isinstance(headers, dict) else [],
            "sensitive_header_keys": sorted(str(key) for key in headers.keys() if str(key).lower() == "authorization" or SENSITIVE_KEY_PATTERN.search(str(key))) if isinstance(headers, dict) else [],
            "env_keys": env_keys,
            "sensitive_env_keys": sensitive_env_keys,
            "installable": item.get("status") == "available" and isinstance(item.get("install_template"), dict),
        }

    def _safe_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        detail = self._summary(item)
        detail["runtime"] = copy.deepcopy(item.get("runtime") or {})
        detail["source_path"] = item.get("_source_path", "")
        return detail


def _url_preview(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
