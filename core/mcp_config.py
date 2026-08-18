"""MCP server configuration loading."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config.settings import settings
from core.mcp_types import MCPServerConfig


@dataclass(frozen=True)
class MCPConfigSourceStatus:
    path: str
    kind: str
    exists: bool
    loaded: bool
    server_count: int
    error_code: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MCPConfigLoadResult:
    configs: list[MCPServerConfig] = field(default_factory=list)
    sources: list[MCPConfigSourceStatus] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "configs": [config.to_dict() for config in self.configs],
            "sources": [source.to_dict() for source in self.sources],
            "errors": list(self.errors),
        }


def load_mcp_server_configs(path: str | Path) -> list[MCPServerConfig]:
    """Load MCP server configs from project or common MCP config formats."""

    configs, _error = _load_configs_from_file(Path(path), allow_fragment=False)
    return configs


def load_mcp_server_configs_from_sources(
    config_path: str | Path | None,
    config_dir: str | Path | None,
) -> MCPConfigLoadResult:
    """Load and merge MCP server configs from a legacy file and a config directory."""

    configs_by_name: dict[str, MCPServerConfig] = {}
    sources: list[MCPConfigSourceStatus] = []
    errors: list[dict[str, Any]] = []

    if config_path:
        path = Path(config_path)
        configs, error = _load_configs_from_file(path, allow_fragment=False)
        sources.append(_source_status(path, "file", path.exists(), configs, error))
        if error:
            errors.append(error)
        _merge_configs(configs_by_name, configs, str(path), errors)

    if config_dir:
        directory = Path(config_dir)
        if not directory.exists():
            sources.append(MCPConfigSourceStatus(str(directory), "directory", False, False, 0, "mcp_config_dir_not_found"))
        elif not directory.is_dir():
            error = {"code": "mcp_config_dir_invalid", "path": str(directory)}
            sources.append(MCPConfigSourceStatus(str(directory), "directory", True, False, 0, error_code=error["code"]))
            errors.append(error)
        else:
            fragments = _config_fragments(directory)
            sources.append(MCPConfigSourceStatus(str(directory), "directory", True, True, len(fragments)))
            for fragment in fragments:
                configs, error = _load_configs_from_file(fragment, allow_fragment=True)
                sources.append(_source_status(fragment, "fragment", True, configs, error))
                if error:
                    errors.append(error)
                _merge_configs(configs_by_name, configs, str(fragment), errors)

    return MCPConfigLoadResult(configs=list(configs_by_name.values()), sources=sources, errors=errors)


def _load_configs_from_file(path: Path, allow_fragment: bool) -> tuple[list[MCPServerConfig], dict[str, Any] | None]:
    if not path.exists():
        return [], {"code": "mcp_config_not_found", "path": str(path)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return [], {"code": "mcp_config_json_invalid", "path": str(path)}
    except OSError as exc:
        return [], {"code": "mcp_config_read_failed", "path": str(path), "error": str(exc)[:300]}
    if not isinstance(raw, dict):
        return [], {"code": "mcp_config_invalid_structure", "path": str(path)}
    configs = _configs_from_raw(raw, allow_fragment=allow_fragment)
    if not configs and allow_fragment:
        return [], {"code": "mcp_config_no_servers", "path": str(path)}
    return configs, None


def _configs_from_raw(raw: dict[str, Any], allow_fragment: bool = False) -> list[MCPServerConfig]:
    if isinstance(raw.get("mcp"), dict):
        return _configs_from_mapping(raw["mcp"])
    if allow_fragment and isinstance(raw.get("server"), dict):
        return [_server_from_mapping(raw["server"], default_enabled=False)]
    if allow_fragment and "name" in raw:
        return [_server_from_mapping(raw, default_enabled=False)]
    return _configs_from_mapping(raw)


def _config_fragments(directory: Path) -> list[Path]:
    candidates = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.name == ".gitkeep":
            continue
        if path.name.endswith(".example.json"):
            continue
        if path.suffix == ".json":
            candidates.append(path)
    return candidates


def _source_status(
    path: Path,
    kind: str,
    exists: bool,
    configs: list[MCPServerConfig],
    error: dict[str, Any] | None,
) -> MCPConfigSourceStatus:
    return MCPConfigSourceStatus(
        path=str(path),
        kind=kind,
        exists=exists,
        loaded=bool(configs) and not error,
        server_count=len(configs),
        error_code=str(error.get("code", "")) if error else "",
        error=str(error.get("error", ""))[:300] if error else "",
    )


def _merge_configs(
    configs_by_name: dict[str, MCPServerConfig],
    configs: list[MCPServerConfig],
    source_path: str,
    errors: list[dict[str, Any]],
) -> None:
    for config in configs:
        if not config.name:
            errors.append({"code": "invalid_server_name", "source": source_path})
            continue
        if config.name in configs_by_name:
            errors.append({"code": "duplicate_server_override", "server_name": config.name, "source": source_path})
        configs_by_name[config.name] = config


def _configs_from_mapping(raw: dict[str, Any]) -> list[MCPServerConfig]:
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("servers"), list):
        return [_server_from_mapping(item) for item in raw["servers"] if isinstance(item, dict)]
    if isinstance(raw.get("servers"), dict):
        return [
            _server_from_mapping({**item, "name": name}, default_enabled=False)
            for name, item in raw["servers"].items()
            if isinstance(item, dict)
        ]
    if isinstance(raw.get("mcpServers"), dict):
        configs: list[MCPServerConfig] = []
        for name, item in raw["mcpServers"].items():
            if isinstance(item, dict):
                configs.append(_server_from_mapping({**item, "name": name}, default_enabled=False))
        return configs
    return []


def _server_from_mapping(data: dict[str, Any], default_enabled: bool = False) -> MCPServerConfig:
    timeout = data.get("timeout_seconds", data.get("timeoutSeconds", settings.mcp_default_timeout_seconds))
    transport = data.get("transport", data.get("type", "stdio"))
    env = {str(key): _expand_env_value(str(value)) for key, value in (data.get("env") or {}).items()}
    headers = {str(key): str(value) for key, value in (data.get("headers") or {}).items()} if isinstance(data.get("headers", {}), dict) else {}
    name = str(data.get("name", "")).strip()
    metadata = dict(data.get("metadata") or {})
    return MCPServerConfig(
        name=name,
        command=str(data.get("command", "")).strip(),
        args=[str(item) for item in data.get("args", []) if item is not None],
        url=str(data.get("url", "")).strip(),
        enabled=bool(data.get("enabled", default_enabled)),
        transport=str(transport or "stdio"),
        env=env,
        headers=headers,
        timeout_seconds=_safe_float(timeout, settings.mcp_default_timeout_seconds),
        permissions=[str(item) for item in data.get("permissions", ["read_only"])],
        metadata=metadata,
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _expand_env_value(value: str) -> str:
    return re.sub(r"\$\{([^}]+)\}", lambda match: os.getenv(match.group(1), ""), value)
