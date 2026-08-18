"""Lightweight MCP client data types without SDK dependencies."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for one MCP server."""

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    enabled: bool = True
    transport: str = "stdio"
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    permissions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MCPToolSpec:
    """Normalized MCP tool specification."""

    name: str
    qualified_name: str
    server_name: str
    description: str
    input_schema: dict[str, Any]
    source: str = "mcp"
    permission_level: str = "read_only"
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def schema_name(self) -> str:
        """Return a provider-neutral safe function schema name."""

        override = str(self.metadata.get("schema_name_override", "")).strip()
        if override:
            return override
        value = re.sub(r"[^A-Za-z0-9_-]+", "_", self.qualified_name).strip("_")
        value = re.sub(r"_+", "_", value)
        return (value or "mcp_tool")[:64]

    def to_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.schema_name(),
                "description": f"{self.description} (MCP tool: {self.qualified_name})".strip(),
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }

    def to_openai_tool_schema(self) -> dict[str, Any]:
        return self.to_tool_schema()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MCPToolCall:
    """One MCP tool call request."""

    qualified_name: str
    arguments: dict[str, Any]
    server_name: str = ""
    tool_name: str = ""


@dataclass(frozen=True)
class MCPToolResult:
    """Standard MCP tool result compatible with local tool result shape."""

    success: bool
    data: Any = None
    error: str = ""
    error_code: str = ""
    source: str = "mcp"
    server_name: str = ""
    tool_name: str = ""
    qualified_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
