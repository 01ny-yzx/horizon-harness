import type {
  ApiClientConfig,
  ApiResponse,
  BrowserClickRequest,
  BrowserScreenshotRequest,
  BrowserUrlRequest,
  ChatRequest,
  ChatResponse,
  ClearMemoryTypeRequest,
  DeleteMemoryReferenceRequest,
  DeleteProjectInstructionRequest,
  DeleteUserPreferenceRequest,
  DocumentLoadRequest,
  MemoryListResponse,
  MCPInstallRequest,
  MCPInstalledItem,
  MCPInstalledResponse,
  MCPInstallResponse,
  MCPMarketplaceItem,
  MCPMarketplaceResponse,
  MCPPermissionUpdateRequest,
  MCPPermissionUpdateResponse,
  MCPPermissionServer,
  MCPPermissionsResponse,
  MCPRuntimeReloadResponse,
  MCPRuntimeDependenciesResponse,
  MCPRuntimeStatusResponse,
  RagQueryRequest,
  RagQueryResponse,
  RememberPreferenceRequest,
  WorkspaceRequest,
  WorkspaceStatusResponse,
} from "../types/api";

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

export function readApiConfig(): ApiClientConfig {
  return {
    baseUrl: localStorage.getItem("agent_api_base_url") || DEFAULT_BASE_URL,
    apiKey: localStorage.getItem("agent_api_key") || "",
    userId: localStorage.getItem("agent_user_id") || "default_user",
    projectId: localStorage.getItem("agent_project_id") || "default_project",
  };
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function createApiClient(config: ApiClientConfig) {
  const messages = config.messages ?? {
    networkError: "Unable to connect to the backend service",
    requestFailed: "Request failed",
    invalidApiKey: "API key is missing or invalid. Check X-API-Key in Settings.",
    quotaExceeded: "Today's quota has been used. Try again later or adjust backend limits.",
    invalidRequest: "Request format is invalid. Check user_id, project_id, and input.",
  };
  const baseUrl = config.baseUrl.replace(/\/$/, "");
  const workspace = (): WorkspaceRequest => ({
    user_id: config.userId || "default_user",
    project_id: config.projectId || "default_project",
  });

  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (!headers.has("Content-Type") && options.body) {
      headers.set("Content-Type", "application/json");
    }
    if (config.apiKey) {
      headers.set("X-API-Key", config.apiKey);
    }

    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        ...options,
        headers,
      });
    } catch (error) {
      throw new ApiError(error instanceof Error ? error.message : messages.networkError, 0);
    }

    const payload = await readPayload(response);
    if (!response.ok) {
      throw new ApiError(normalizeError(response.status, payload, messages), response.status);
    }

    if (payload && typeof payload === "object" && "success" in payload && payload.success === false) {
      const message = extractErrorMessage(payload.error, messages.requestFailed);
      throw new ApiError(message, response.status);
    }

    return payload as T;
  }

  function post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  function deleteRequest<T>(path: string): Promise<T> {
    return request<T>(path, { method: "DELETE" });
  }

  return {
    artifactDownloadUrl: (artifactId: string) => `${baseUrl}/files/${encodeURIComponent(artifactId)}`,
    health: () => request<ApiResponse>("/health"),
    status: () => request<ApiResponse>("/status"),
    chat: (body: Pick<ChatRequest, "message" | "debug">) => post<ChatResponse>("/chat", { ...workspace(), ...body }),
    getWorkspaceStatus: () => post<WorkspaceStatusResponse>("/workspace/status", workspace()),
    listMemory: (body: Partial<WorkspaceRequest> & { memory_type?: string } = {}) =>
      post<ApiResponse<MemoryListResponse>>("/memory/list", { ...workspace(), memory_type: "all", ...body }),
    deleteMemoryReference: (body: Omit<DeleteMemoryReferenceRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/memory/reference/delete", { ...workspace(), ...body }),
    deleteUserPreference: (body: Omit<DeleteUserPreferenceRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/memory/preference/delete", { ...workspace(), ...body }),
    deleteProjectInstruction: (body: Omit<DeleteProjectInstructionRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/memory/project-instruction/delete", { ...workspace(), ...body }),
    clearMemoryType: (body: Omit<ClearMemoryTypeRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/memory/type/clear", { ...workspace(), ...body }),
    rememberPreference: (body: Omit<RememberPreferenceRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/memory/remember/preference", { ...workspace(), ...body }),
    loadDocument: (body: Omit<DocumentLoadRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/documents/load", { ...workspace(), ...body }),
    listDocuments: () => post<ApiResponse>("/documents/list", workspace()),
    ragQuery: (body: Omit<RagQueryRequest, "user_id" | "project_id">) =>
      post<ApiResponse<RagQueryResponse>>("/rag/query", { ...workspace(), ...body }),
    ragStatus: () => post<ApiResponse>("/rag/status", workspace()),
    usageStatus: () => post<ApiResponse>("/usage/status", workspace()),
    cacheStatus: () => post<ApiResponse>("/cache/status"),
    browserStatus: () => post<ApiResponse>("/browser/status", workspace()),
    browserExtractText: (body: Omit<BrowserUrlRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/browser/extract-text", { ...workspace(), ...body }),
    browserListLinks: (body: Omit<BrowserUrlRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/browser/list-links", { ...workspace(), ...body }),
    browserScreenshot: (body: Omit<BrowserScreenshotRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/browser/screenshot", { ...workspace(), ...body }),
    browserClickAndExtract: (body: Omit<BrowserClickRequest, "user_id" | "project_id">) =>
      post<ApiResponse>("/browser/click-and-extract", { ...workspace(), ...body }),
    mcpMarketplace: () => request<ApiResponse<MCPMarketplaceResponse>>("/mcp/marketplace"),
    mcpMarketplaceItem: (itemId: string) => request<ApiResponse<MCPMarketplaceItem>>(`/mcp/marketplace/${encodeURIComponent(itemId)}`),
    mcpInstalled: () => request<ApiResponse<MCPInstalledResponse>>("/mcp/installed"),
    mcpInstalledItem: (serverName: string) => request<ApiResponse<MCPInstalledItem>>(`/mcp/installed/${encodeURIComponent(serverName)}`),
    installMcp: (itemId: string, body?: MCPInstallRequest) =>
      post<ApiResponse<MCPInstallResponse>>(`/mcp/marketplace/${encodeURIComponent(itemId)}/install`, {
        enabled: body?.enabled ?? true,
        permissions: body?.permissions ?? null,
      }),
    enableMcp: (serverName: string) => post<ApiResponse<MCPInstallResponse>>(`/mcp/installed/${encodeURIComponent(serverName)}/enable`),
    disableMcp: (serverName: string) => post<ApiResponse<MCPInstallResponse>>(`/mcp/installed/${encodeURIComponent(serverName)}/disable`),
    uninstallMcp: (serverName: string) => deleteRequest<ApiResponse<MCPInstallResponse>>(`/mcp/installed/${encodeURIComponent(serverName)}`),
    mcpRuntimeStatus: () => request<ApiResponse<MCPRuntimeStatusResponse>>("/mcp/runtime/status"),
    reloadMcpRuntime: () => post<ApiResponse<MCPRuntimeReloadResponse>>("/mcp/runtime/reload"),
    mcpRuntimeDependencies: () => request<ApiResponse<MCPRuntimeDependenciesResponse>>("/mcp/runtime/dependencies"),
    mcpPermissions: () => request<ApiResponse<MCPPermissionsResponse>>("/mcp/permissions"),
    mcpPermissionServer: (serverName: string) => request<ApiResponse<MCPPermissionServer>>(`/mcp/permissions/${encodeURIComponent(serverName)}`),
    updateMcpPermissions: (serverName: string, body: MCPPermissionUpdateRequest) =>
      post<ApiResponse<MCPPermissionUpdateResponse>>(`/mcp/permissions/${encodeURIComponent(serverName)}`, body),
  };
}

async function readPayload(response: Response): Promise<any> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function normalizeError(status: number, payload: any, messages: NonNullable<ApiClientConfig["messages"]>): string {
  if (status === 401) {
    return messages.invalidApiKey;
  }
  if (status === 429) {
    return messages.quotaExceeded;
  }
  if (status === 422) {
    return messages.invalidRequest;
  }
  if (payload && typeof payload === "object") {
    const detail = payload.detail ?? payload.error;
    const message = extractErrorMessage(detail, "");
    if (message) {
      return message;
    }
    if (detail) {
      return JSON.stringify(detail);
    }
  }
  return typeof payload === "string" ? payload : `${messages.requestFailed} (${status})`;
}

function extractErrorMessage(error: unknown, fallback: string): string {
  if (typeof error === "string") {
    return error;
  }
  if (error && typeof error === "object") {
    const record = error as { message?: unknown; code?: unknown };
    if (typeof record.message === "string") {
      return record.message;
    }
    if (typeof record.code === "string") {
      return record.code;
    }
  }
  return fallback;
}
