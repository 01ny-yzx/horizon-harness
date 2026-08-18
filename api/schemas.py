"""Pydantic request and response schemas for Service API v1."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from config.settings import settings


class WorkspaceRequest(BaseModel):
    user_id: str = Field(default=settings.default_user_id)
    project_id: str = Field(default=settings.default_project_id)


class ChatRequest(WorkspaceRequest):
    message: str
    debug: bool = False


class ChatResponse(BaseModel):
    success: bool
    answer: str
    user_id: str
    project_id: str
    trace_id: str | None = None
    remaining: int | None = None
    rate_limit: dict[str, Any] | None = None
    error: str | None = None


class WorkspaceStatusResponse(BaseModel):
    success: bool
    user_id: str
    project_id: str
    workspace_id: str
    memory_counts: dict[str, int]
    documents_count: int
    chunks_count: int
    vectors_count: int


class MemoryListRequest(WorkspaceRequest):
    memory_type: str = "all"


class ForgetMemoryRequest(WorkspaceRequest):
    memory_type: str = "all"
    keyword: str


class RememberPreferenceRequest(WorkspaceRequest):
    key: str
    value: str


class DocumentLoadRequest(WorkspaceRequest):
    path: str
    create_chunks: bool = True


class DirectoryLoadRequest(WorkspaceRequest):
    path: str
    recursive: bool = False
    max_files: int = 20
    create_chunks: bool = True


class ChunkSearchRequest(WorkspaceRequest):
    keyword: str
    document_id: str | None = None
    limit: int = 5


class RagQueryRequest(WorkspaceRequest):
    query: str
    mode: str = "hybrid"
    top_k: int = 5


class BrowserUrlRequest(WorkspaceRequest):
    url: str


class BrowserScreenshotRequest(BrowserUrlRequest):
    full_page: bool = False


class BrowserClickRequest(BrowserUrlRequest):
    selector: str | None = None
    text: str | None = None


class MCPInstallRequest(BaseModel):
    enabled: bool = True
    permissions: list[str] | None = None


class MCPPermissionUpdateRequest(BaseModel):
    permissions: list[str]


class MCPRuntimeDiff(BaseModel):
    added_tools: list[str] = Field(default_factory=list)
    removed_tools: list[str] = Field(default_factory=list)
    unchanged_tools: list[str] = Field(default_factory=list)


class MCPRuntimeStatusResponse(BaseModel):
    runtime: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    errors: list[Any] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class MCPRuntimeReloadResponse(BaseModel):
    success: bool
    reload_count: int = 0
    last_reload_at: str | None = None
    last_reload_success: bool = False
    last_reload_error: str = ""
    previous: dict[str, Any] = Field(default_factory=dict)
    current: dict[str, Any] = Field(default_factory=dict)
    diff: MCPRuntimeDiff = Field(default_factory=MCPRuntimeDiff)
    status: dict[str, Any] = Field(default_factory=dict)


class MCPDependencyStatus(BaseModel):
    name: str = ""
    command: str = ""
    found: bool = False
    resolved_path: str = ""
    command_source: str = ""
    version: str = ""
    error_code: str = ""
    error: str = ""
    suggested_fix: str = ""


class MCPRuntimeDependenciesResponse(BaseModel):
    docker: dict[str, Any] = Field(default_factory=dict)
    node: dict[str, Any] = Field(default_factory=dict)
    npm: dict[str, Any] = Field(default_factory=dict)
    npx: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, Any] = Field(default_factory=dict)


class ApiResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: Any | None = None


class ArtifactInfo(BaseModel):
    artifact_id: str
    download_url: str
    filename: str
