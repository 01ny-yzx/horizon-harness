"""Install, update, and remove local MCP config fragments."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from config.settings import settings
from core.mcp_permissions import MCP_PERMISSION_LEVELS


SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SENSITIVE_KEY_PATTERN = re.compile(r"(token|api[_-]?key|apikey|secret|password)", re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
ANY_PLACEHOLDER_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
REAL_SECRET_HINT = re.compile(r"(ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|sk-[A-Za-z0-9_-]{12,})")
SAFE_METADATA_KEYS = {
    "installed_by",
    "installed_at",
    "install_version",
    "description",
    "provider",
    "mode",
    "image",
    "type",
    "marketplace_item_id",
    "marketplace_version",
    "tool_aliases",
}


class MCPInstallManager:
    """Manage local MCP fragments under config/mcp.d."""

    def __init__(self, config_dir: str | Path = settings.mcp_config_dir) -> None:
        self.config_dir = Path(config_dir)

    def install_mcp(
        self,
        server_name: str,
        *,
        command: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        permissions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        transport: str = "stdio",
        enabled: bool = True,
        url: str = "",
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        valid = self._validate_server_name(server_name)
        if valid:
            return valid
        secret_error = self._validate_env(env or {})
        if secret_error:
            return secret_error
        header_error = self._validate_headers(headers or {})
        if header_error:
            return header_error

        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self._config_path(server_name)
        existed = path.exists()
        install_metadata = dict(metadata or {})
        install_metadata.setdefault("installed_by", "mcp_install_manager")
        install_metadata.setdefault("install_version", "1")
        install_metadata.setdefault("installed_at", datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"))

        server_config: dict[str, Any] = {
            "transport": transport,
            "enabled": bool(enabled),
            "command": str(command),
            "args": [str(item) for item in (args or [])],
            "env": {str(key): str(value) for key, value in (env or {}).items()},
            "permissions": [str(item) for item in (permissions or ["read_only"])],
            "metadata": install_metadata,
        }
        if url:
            server_config["url"] = str(url)
        if headers:
            server_config["headers"] = {str(key): str(value) for key, value in headers.items()}
        if timeout_seconds is not None:
            server_config["timeout_seconds"] = float(timeout_seconds)

        payload = {"servers": {server_name: server_config}}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "success": True,
            "status": "updated" if existed else "installed",
            "server_name": server_name,
            "config_path": str(path),
        }

    def uninstall_mcp(self, server_name: str) -> dict[str, Any]:
        valid = self._validate_server_name(server_name)
        if valid:
            return valid
        path = self._config_path(server_name)
        if not path.exists():
            return {"success": True, "status": "not_found", "server_name": server_name, "config_path": str(path)}
        path.unlink()
        return {"success": True, "status": "uninstalled", "server_name": server_name, "config_path": str(path)}

    def enable_mcp(self, server_name: str) -> dict[str, Any]:
        return self._set_enabled(server_name, True)

    def disable_mcp(self, server_name: str) -> dict[str, Any]:
        return self._set_enabled(server_name, False)

    def update_mcp_permissions(self, server_name: str, permissions: list[str]) -> dict[str, Any]:
        valid = self._validate_server_name(server_name)
        if valid:
            return valid
        permission_error = self._validate_permissions(permissions)
        if permission_error:
            return permission_error
        path = self._config_path(server_name)
        if not path.exists():
            return {
                "success": False,
                "status": "not_found",
                "server_name": server_name,
                "config_path": str(path),
                "error_code": "mcp_server_not_found",
                "error": "MCP server not found.",
            }
        raw = self._read_json(path)
        if raw.get("error_code"):
            return {"success": False, "status": "error", "server_name": server_name, "config_path": str(path), **raw}
        data = raw["data"]
        config = self._get_server_config(data, server_name)
        if config is None:
            return {
                "success": False,
                "status": "not_found",
                "server_name": server_name,
                "config_path": str(path),
                "error_code": "mcp_server_not_found",
            }
        config["permissions"] = [str(item) for item in permissions]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "success": True,
            "status": "permissions_updated",
            "server_name": server_name,
            "permissions": [str(item) for item in permissions],
            "config_path": str(path),
        }

    def list_installed_mcp(self) -> list[dict[str, Any]]:
        if not self.config_dir.exists():
            return []
        entries: list[dict[str, Any]] = []
        for path in sorted(self.config_dir.iterdir(), key=lambda item: item.name):
            if not self._is_managed_config_file(path):
                continue
            raw = self._read_json(path)
            if raw.get("error_code"):
                entries.append(
                    {
                        "server_name": path.name.removesuffix(".local.json"),
                        "config_path": str(path),
                        "source": "mcp_install_manager",
                        "error_code": raw["error_code"],
                        "error": raw.get("error", ""),
                    }
                )
                continue
            for server_name, config in self._server_items(raw["data"]):
                entries.append(self._summary(server_name, config, path))
        return entries

    def read_installed_mcp(self, server_name: str) -> dict[str, Any]:
        valid = self._validate_server_name(server_name)
        if valid:
            return valid
        path = self._config_path(server_name)
        if not path.exists():
            return {"success": False, "status": "not_found", "server_name": server_name, "config_path": str(path)}
        raw = self._read_json(path)
        if raw.get("error_code"):
            return {"success": False, "status": "error", "server_name": server_name, "config_path": str(path), **raw}
        config = self._get_server_config(raw["data"], server_name)
        if config is None:
            return {
                "success": False,
                "status": "not_found",
                "server_name": server_name,
                "config_path": str(path),
                "error_code": "mcp_server_not_found",
            }
        return {"success": True, "status": "found", **self._summary(server_name, config, path)}

    def _set_enabled(self, server_name: str, enabled: bool) -> dict[str, Any]:
        valid = self._validate_server_name(server_name)
        if valid:
            return valid
        path = self._config_path(server_name)
        if not path.exists():
            return {"success": False, "status": "not_found", "server_name": server_name, "config_path": str(path)}
        raw = self._read_json(path)
        if raw.get("error_code"):
            return {"success": False, "status": "error", "server_name": server_name, "config_path": str(path), **raw}
        data = raw["data"]
        config = self._get_server_config(data, server_name)
        if config is None:
            return {
                "success": False,
                "status": "not_found",
                "server_name": server_name,
                "config_path": str(path),
                "error_code": "mcp_server_not_found",
            }
        config["enabled"] = bool(enabled)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "success": True,
            "status": "enabled" if enabled else "disabled",
            "server_name": server_name,
            "config_path": str(path),
        }

    def _validate_server_name(self, server_name: str) -> dict[str, Any] | None:
        name = str(server_name or "")
        if not name or not SERVER_NAME_PATTERN.fullmatch(name):
            return {
                "success": False,
                "status": "invalid_server_name",
                "server_name": server_name,
                "error_code": "invalid_server_name",
                "error": "server_name must match ^[A-Za-z0-9_-]+$",
            }
        return None

    def _validate_env(self, env: dict[str, str]) -> dict[str, Any] | None:
        for key, value in env.items():
            text = str(value)
            if SENSITIVE_KEY_PATTERN.search(str(key)) and text and not PLACEHOLDER_PATTERN.fullmatch(text):
                return {
                    "success": False,
                    "status": "secret_rejected",
                    "error_code": "mcp_secret_value_rejected",
                    "error": f"env value for {key} must be empty or an environment placeholder",
                }
        return None

    def _validate_permissions(self, permissions: list[str]) -> dict[str, Any] | None:
        if not isinstance(permissions, list) or not permissions:
            return {"success": False, "status": "invalid_permissions", "error_code": "invalid_mcp_permissions", "error": "permissions must be a non-empty list"}
        normalized = [str(item).strip().lower() for item in permissions if str(item).strip()]
        if not normalized:
            return {"success": False, "status": "invalid_permissions", "error_code": "invalid_mcp_permissions", "error": "permissions must be a non-empty list"}
        unknown = [item for item in normalized if item not in MCP_PERMISSION_LEVELS]
        if unknown:
            return {"success": False, "status": "invalid_permissions", "error_code": "invalid_mcp_permissions", "error": "unknown MCP permission"}
        if "dangerous" in normalized:
            return {
                "success": False,
                "status": "dangerous_permission_not_allowed",
                "error_code": "dangerous_permission_not_allowed",
                "error": "dangerous permission cannot be enabled from the permission panel",
            }
        return None

    def _config_path(self, server_name: str) -> Path:
        root = self.config_dir.resolve()
        path = (root / f"{server_name}.local.json").resolve()
        if path.parent != root:
            raise ValueError("resolved MCP config path escaped config_dir")
        return path

    def _is_managed_config_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.name == ".gitkeep":
            return False
        if path.name.endswith(".example.json"):
            return False
        return path.name.endswith(".local.json")

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {"error_code": "mcp_config_json_invalid", "error": "Invalid JSON"}
        except OSError as exc:
            return {"error_code": "mcp_config_read_failed", "error": str(exc)[:300]}
        if not isinstance(data, dict):
            return {"error_code": "mcp_config_invalid_structure", "error": "Config must be a JSON object"}
        return {"data": data}

    def _server_items(self, data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        servers = data.get("servers")
        if isinstance(servers, dict):
            return [(str(name), config) for name, config in servers.items() if isinstance(config, dict)]
        server = data.get("server")
        if isinstance(server, dict):
            name = str(server.get("name") or "")
            return [(name, server)] if name else []
        if "name" in data:
            name = str(data.get("name") or "")
            return [(name, data)] if name else []
        return []

    def _get_server_config(self, data: dict[str, Any], server_name: str) -> dict[str, Any] | None:
        servers = data.get("servers")
        if isinstance(servers, dict) and isinstance(servers.get(server_name), dict):
            return servers[server_name]
        for name, config in self._server_items(data):
            if name == server_name:
                return config
        return None

    def _summary(self, server_name: str, config: dict[str, Any], path: Path) -> dict[str, Any]:
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
        args = config.get("args") if isinstance(config.get("args"), list) else []
        return {
            "server_name": server_name,
            "enabled": bool(config.get("enabled", False)),
            "config_path": str(path),
            "transport": str(config.get("transport", config.get("type", "stdio"))),
            "command": str(config.get("command", "")),
            "url_preview": _url_preview(str(config.get("url", ""))),
            "args_count": len(args),
            "has_env": bool(env),
            "env_keys": sorted(str(key) for key in env.keys()),
            "has_headers": bool(headers),
            "header_keys": sorted(str(key) for key in headers.keys()),
            "sensitive_header_keys": sorted(str(key) for key in headers.keys() if _is_sensitive_header(str(key))),
            "permissions": [str(item) for item in config.get("permissions", [])],
            "metadata": self._sanitize_mapping(config.get("metadata") if isinstance(config.get("metadata"), dict) else {}),
            "source": "mcp_install_manager",
        }

    def _validate_headers(self, headers: dict[str, str]) -> dict[str, Any] | None:
        for key, value in headers.items():
            text_key = str(key)
            text = str(value)
            if (_is_sensitive_header(text_key) or REAL_SECRET_HINT.search(text)) and text and not ANY_PLACEHOLDER_PATTERN.search(text):
                return {
                    "success": False,
                    "status": "secret_rejected",
                    "error_code": "mcp_secret_value_rejected",
                    "error": f"header value for {text_key} must be empty or contain an environment placeholder",
                }
        return None

    def _sanitize_mapping(self, data: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in data.items():
            text_key = str(key)
            if SENSITIVE_KEY_PATTERN.search(text_key):
                safe[text_key] = "[redacted]"
            elif text_key == "tool_aliases" and isinstance(value, dict):
                safe[text_key] = {str(alias): str(tool_name) for alias, tool_name in value.items()}
            elif text_key in SAFE_METADATA_KEYS or isinstance(value, (str, int, float, bool)) or value is None:
                safe[text_key] = value
            else:
                safe[text_key] = str(value)
        return safe


def _is_sensitive_header(key: str) -> bool:
    return str(key).lower() == "authorization" or bool(SENSITIVE_KEY_PATTERN.search(str(key)))


def _url_preview(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
