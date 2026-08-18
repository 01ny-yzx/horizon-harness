export type Role = "user" | "assistant" | "system" | "error";

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  createdAt: string;
}

export interface WorkspaceRequest {
  user_id: string;
  project_id: string;
}

export interface ChatRequest extends WorkspaceRequest {
  message: string;
  debug?: boolean;
}

export interface ChatResponse {
  success: boolean;
  answer: string;
  user_id: string;
  project_id: string;
  trace_id?: string | null;
  remaining?: number | null;
  rate_limit?: Record<string, unknown> | null;
  error?: string | null;
}

export interface WorkspaceStatusResponse {
  success: boolean;
  user_id: string;
  project_id: string;
  workspace_id: string;
  memory_counts: Record<string, number>;
  documents_count: number;
  chunks_count: number;
  vectors_count: number;
}

export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T | null;
  error?: string | { code?: string; message?: string } | null;
}

export interface MemoryListResponse {
  user?: unknown;
  project?: unknown;
  tasks?: unknown;
  [key: string]: unknown;
}

export interface RagQueryResponse {
  answer?: string;
  evidence?: unknown[];
  citations?: unknown[];
  chunks?: unknown[];
  [key: string]: unknown;
}

export interface ApiClientConfig {
  baseUrl: string;
  apiKey?: string;
  userId: string;
  projectId: string;
  messages?: {
    networkError: string;
    requestFailed: string;
    invalidApiKey: string;
    quotaExceeded: string;
    invalidRequest: string;
  };
}

export interface DocumentLoadRequest extends WorkspaceRequest {
  path: string;
  create_chunks?: boolean;
}

export interface ForgetMemoryRequest extends WorkspaceRequest {
  memory_type: string;
  keyword: string;
}

export interface RememberPreferenceRequest extends WorkspaceRequest {
  key: string;
  value: string;
}

export interface RagQueryRequest extends WorkspaceRequest {
  query: string;
  mode: "hybrid" | "semantic" | "keyword";
  top_k: number;
}

export interface BrowserUrlRequest extends WorkspaceRequest {
  url: string;
}

export interface BrowserScreenshotRequest extends BrowserUrlRequest {
  full_page?: boolean;
}

export interface BrowserClickRequest extends BrowserUrlRequest {
  selector?: string | null;
  text?: string | null;
}

export interface MCPPermissionSet {
  default?: string[];
  available?: string[];
}

export interface MCPMarketplaceItem {
  id?: string;
  server_name: string;
  display_name?: string;
  description?: string;
  category?: string;
  status?: "available" | "coming_soon" | "disabled" | string;
  official?: boolean;
  version?: string;
  tags?: string[];
  permissions?: MCPPermissionSet;
  transport?: string;
  url_preview?: string;
  header_keys?: string[];
  sensitive_header_keys?: string[];
  runtime?: {
    type?: string;
    requires?: string[];
    [key: string]: unknown;
  };
  resource_scope?: {
    type?: string;
    description?: string;
    [key: string]: unknown;
  };
  env_keys?: string[];
  sensitive_env_keys?: string[];
  installed?: boolean;
  installed_enabled?: boolean | null;
  installable?: boolean;
}

export interface MCPInstalledItem {
  server_name: string;
  enabled?: boolean;
  config_path?: string;
  transport?: string;
  command?: string;
  url_preview?: string;
  args_count?: number;
  has_env?: boolean;
  env_keys?: string[];
  has_headers?: boolean;
  header_keys?: string[];
  sensitive_header_keys?: string[];
  permissions?: string[];
  metadata?: Record<string, unknown>;
  source?: string;
  error?: string;
}

export interface MCPMarketplaceResponse {
  items: MCPMarketplaceItem[];
  installed?: MCPInstalledItem[];
  counts?: {
    catalog_total?: number;
    available?: number;
    installed?: number;
    [key: string]: number | undefined;
  };
  errors?: unknown[];
  diagnostics?: unknown;
}

export interface MCPInstalledResponse {
  items: MCPInstalledItem[];
}

export interface MCPInstallRequest {
  enabled?: boolean;
  permissions?: string[] | null;
}

export interface MCPInstallResponse {
  result?: unknown;
  installed?: unknown;
  runtime_reload_required?: boolean;
  hot_reload?: boolean;
  reload_endpoint?: string;
}

export interface MCPRuntimeStatus {
  enabled?: boolean;
  reload_count?: number;
  last_reload_at?: string | null;
  last_reload_success?: boolean;
  last_reload_error?: string;
  servers_total?: number;
  servers_enabled?: number;
  tools_total?: number;
  tools_enabled?: number;
  tools_disabled?: number;
  tool_names?: string[];
  errors?: unknown[];
  diagnostics?: unknown;
  [key: string]: unknown;
}

export interface MCPRuntimeDiff {
  added_tools?: string[];
  removed_tools?: string[];
  unchanged_tools?: string[];
}

export interface MCPRuntimeStatusResponse {
  runtime?: MCPRuntimeStatus;
  config?: {
    config_path?: string;
    config_dir?: string;
    [key: string]: unknown;
  };
  errors?: unknown[];
  diagnostics?: unknown;
}

export interface MCPRuntimeReloadResult {
  success?: boolean;
  reload_count?: number;
  last_reload_at?: string | null;
  last_reload_success?: boolean;
  last_reload_error?: string;
  previous?: Record<string, unknown>;
  current?: Record<string, unknown>;
  diff?: MCPRuntimeDiff;
  status?: MCPRuntimeStatus;
}

export interface MCPRuntimeReloadResponse extends MCPRuntimeReloadResult {
}

export interface MCPDependencyStatus {
  name?: string;
  command?: string;
  found?: boolean;
  resolved_path?: string;
  command_source?: string;
  version?: string;
  error_code?: string;
  error?: string;
  suggested_fix?: string;
  daemon_available?: boolean;
  [key: string]: unknown;
}

export interface MCPRuntimeDependenciesResponse {
  docker?: MCPDependencyStatus;
  node?: MCPDependencyStatus;
  npm?: MCPDependencyStatus;
  npx?: MCPDependencyStatus;
  env?: Record<string, { configured?: boolean; exists?: boolean; value_preview?: string }>;
  [key: string]: unknown;
}

export type MCPPermissionLevel = "read_only" | "write" | "network" | "shell" | "dangerous" | string;

export interface MCPPermissionTool {
  name?: string;
  qualified_name?: string;
  description?: string;
  permission_level?: MCPPermissionLevel;
  enabled?: boolean;
  disabled_reason?: string;
  schema_name?: string;
}

export interface MCPPermissionServer {
  server_name: string;
  installed?: boolean;
  enabled?: boolean;
  permissions?: string[];
  available_permissions?: string[];
  tools_total?: number;
  tools_enabled?: number;
  tools_disabled?: number;
  permission_summary?: Record<string, number>;
  disabled_reasons?: string[];
  env_keys?: string[];
  sensitive_env_keys?: string[];
  url_preview?: string;
  header_keys?: string[];
  sensitive_header_keys?: string[];
  resource_scope?: {
    type?: string;
    description?: string;
    path_preview?: string;
  };
  tools?: MCPPermissionTool[];
}

export interface MCPPermissionSummary {
  servers_total?: number;
  tools_total?: number;
  tools_enabled?: number;
  tools_disabled?: number;
  permission_counts?: Record<string, number>;
  [key: string]: unknown;
}

export interface MCPPermissionPolicyInfo {
  levels?: string[];
  dangerous_disabled?: boolean;
  requires_reload_after_change?: boolean;
  [key: string]: unknown;
}

export interface MCPPermissionsResponse {
  servers?: MCPPermissionServer[];
  summary?: MCPPermissionSummary;
  policy?: MCPPermissionPolicyInfo;
}

export interface MCPPermissionUpdateRequest {
  permissions: string[];
}

export interface MCPPermissionUpdateResponse {
  server_name?: string;
  permissions?: string[];
  runtime_reload_required?: boolean;
  hot_reload?: boolean;
  reload_endpoint?: string;
}
