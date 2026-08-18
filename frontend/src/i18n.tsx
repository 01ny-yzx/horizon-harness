import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Language = "en" | "zh" | "ja";

type TranslationKey =
  | "app.subtitle"
  | "actions.cacheStatus"
  | "actions.cleanup"
  | "actions.extractText"
  | "actions.forget"
  | "actions.install"
  | "actions.updatePermissions"
  | "actions.applyPermissions"
  | "actions.expandTools"
  | "actions.collapseTools"
  | "actions.list"
  | "actions.listLinks"
  | "actions.listMemory"
  | "actions.load"
  | "actions.newChat"
  | "actions.query"
  | "actions.refresh"
  | "actions.refreshStatus"
  | "actions.reloadRuntime"
  | "actions.screenshot"
  | "actions.send"
  | "actions.status"
  | "actions.testConnection"
  | "actions.uninstall"
  | "actions.enable"
  | "actions.disable"
  | "actions.usageStatus"
  | "common.available"
  | "common.category"
  | "common.comingSoon"
  | "common.configured"
  | "common.dependencies"
  | "common.disabled"
  | "common.empty"
  | "common.enabled"
  | "common.environment"
  | "common.error"
  | "common.installed"
  | "common.lastReload"
  | "common.loading"
  | "common.notInstalled"
  | "common.official"
  | "common.permissions"
  | "common.permissionLevel"
  | "common.currentPermissions"
  | "common.availablePermissions"
  | "common.resourceScope"
  | "common.disabledReason"
  | "common.toolsEnabled"
  | "common.toolsDisabled"
  | "common.readOnly"
  | "common.write"
  | "common.network"
  | "common.shell"
  | "common.dangerous"
  | "common.reload"
  | "common.reloadCount"
  | "common.runtime"
  | "common.servers"
  | "common.success"
  | "common.status"
  | "common.tags"
  | "common.tools"
  | "chat.empty"
  | "chat.placeholder"
  | "chat.thinking"
  | "common.unknown"
  | "common.version"
  | "common.failed"
  | "confirm.mcpUninstall"
  | "errors.browserRequest"
  | "errors.browserStatus"
  | "errors.cacheStatus"
  | "errors.connectionTest"
  | "errors.invalidApiKey"
  | "errors.invalidRequest"
  | "errors.documentList"
  | "errors.documentLoad"
  | "errors.memoryForget"
  | "errors.memoryRead"
  | "errors.mcpMarketplace"
  | "errors.mcpPermissions"
  | "errors.mcpPermissionUpdate"
  | "errors.mcpRuntimeReload"
  | "errors.network"
  | "errors.ragQuery"
  | "errors.ragStatus"
  | "errors.requestFailed"
  | "errors.quotaExceeded"
  | "errors.send"
  | "errors.usageStatus"
  | "errors.workspaceStatus"
  | "fields.apiBaseUrl"
  | "fields.apiKey"
  | "fields.documentPath"
  | "fields.forgetKeyword"
  | "fields.llmApiKeyConfigured"
  | "fields.llmBaseUrl"
  | "fields.llmModel"
  | "fields.llmProvider"
  | "fields.reasoningMode"
  | "fields.reasoningAutoPolicy"
  | "fields.extraBodyConfigured"
  | "fields.capabilityPreset"
  | "fields.capabilityPresetRequested"
  | "fields.capabilityPresetEffective"
  | "fields.capabilityPresetSource"
  | "fields.supportsTools"
  | "fields.supportsReasoning"
  | "fields.requiresReasoningEcho"
  | "fields.supportsDisableReasoning"
  | "fields.supportsExtraBody"
  | "fields.supportsJsonMode"
  | "fields.supportsStreaming"
  | "fields.maxContextTokens"
  | "fields.mode"
  | "fields.query"
  | "fields.temperature"
  | "fields.timeout"
  | "fields.url"
  | "language.label"
  | "language.en"
  | "language.zh"
  | "language.ja"
  | "nav.browser"
  | "nav.chat"
  | "nav.documents"
  | "nav.memory"
  | "nav.mcpMarketplace"
  | "nav.rag"
  | "nav.settings"
  | "nav.usage"
  | "nav.workspace"
  | "panels.backendLlmConfig"
  | "panels.browser"
  | "panels.documents"
  | "panels.memory"
  | "panels.mcpMarketplace"
  | "panels.mcpPermissions"
  | "panels.rag"
  | "panels.settings"
  | "panels.usageCache"
  | "panels.workspace"
  | "settings.apiKeyPlaceholder"
  | "settings.hideApiKey"
  | "settings.showApiKey"
  | "status.cache"
  | "status.documents"
  | "status.chunks"
  | "status.noRecentErrors"
  | "status.noWorkspace"
  | "status.recentError"
  | "status.title"
  | "status.usage"
  | "status.vectors"
  | "status.workspace"
  | "text.browserDefault"
  | "text.browserPurpose"
  | "text.browserSystem"
  | "text.documentsHelp"
  | "text.mcpComingSoon"
  | "text.mcpMarketplaceHelp"
  | "text.mcpNoSecrets"
  | "text.mcpPermissionsHelp"
  | "text.mcpPermissionsReloadRequired"
  | "text.mcpDangerousPermissionBlocked"
  | "text.mcpNoToolsForServer"
  | "text.mcpReloadRequiredAfterConfigChange"
  | "text.mcpReloadRuntimeHelp"
  | "text.mcpRuntimeDisabled"
  | "text.mcpDependencyDiagnosticsHelp"
  | "text.mcpDependencySuggestedFix"
  | "text.mcpRuntimeReloadSuccess"
  | "text.mcpRuntimeReloadRequired"
  | "text.todayUsage"
  | "values.no"
  | "values.yes"
  | "values.auto"
  | "values.autoDetectedFromModel"
  | "values.manualOverride"
  | "values.providerDefault"
  | "values.runtimeOverride"
  | "values.safeDefault";

const translations: Record<Language, Record<TranslationKey, string>> = {
  en: {
    "app.subtitle": "Frontend MVP",
    "actions.cacheStatus": "Cache Status",
    "actions.cleanup": "Cleanup",
    "actions.extractText": "Extract Text",
    "actions.forget": "Forget",
    "actions.install": "Install",
    "actions.updatePermissions": "Update Permissions",
    "actions.applyPermissions": "Apply Permissions",
    "actions.expandTools": "Expand Tools",
    "actions.collapseTools": "Collapse Tools",
    "actions.list": "List",
    "actions.listLinks": "List Links",
    "actions.listMemory": "List Memory",
    "actions.load": "Load",
    "actions.newChat": "New Chat",
    "actions.query": "Query",
    "actions.refresh": "Refresh",
    "actions.refreshStatus": "Refresh Status",
    "actions.reloadRuntime": "Reload Runtime",
    "actions.screenshot": "Screenshot",
    "actions.send": "Send",
    "actions.status": "Status",
    "actions.testConnection": "Test Connection",
    "actions.uninstall": "Uninstall",
    "actions.enable": "Enable",
    "actions.disable": "Disable",
    "actions.usageStatus": "Usage Status",
    "common.available": "Available",
    "common.category": "Category",
    "common.comingSoon": "Coming soon",
    "common.configured": "Configured",
    "common.dependencies": "Dependencies",
    "common.disabled": "Disabled",
    "common.empty": "No items to show.",
    "common.enabled": "Enabled",
    "common.environment": "Environment",
    "common.error": "Error",
    "common.installed": "Installed",
    "common.lastReload": "Last reload",
    "common.loading": "Loading...",
    "common.notInstalled": "Not installed",
    "common.official": "Official",
    "common.permissions": "Permissions",
    "common.permissionLevel": "Permission level",
    "common.currentPermissions": "Current permissions",
    "common.availablePermissions": "Available permissions",
    "common.resourceScope": "Resource scope",
    "common.disabledReason": "Disabled reason",
    "common.toolsEnabled": "Tools enabled",
    "common.toolsDisabled": "Tools disabled",
    "common.readOnly": "Read permission",
    "common.write": "Write permission",
    "common.network": "Network",
    "common.shell": "Shell",
    "common.dangerous": "Dangerous",
    "common.reload": "Reload",
    "common.reloadCount": "Reload count",
    "common.runtime": "Runtime",
    "common.servers": "Servers",
    "common.success": "Success",
    "common.status": "Status",
    "common.tags": "Tags",
    "common.tools": "Tools",
    "chat.empty": "Start a new conversation with your local Agent API.",
    "chat.placeholder": "Message Agent...",
    "chat.thinking": "Thinking...",
    "common.unknown": "unknown",
    "common.version": "Version",
    "common.failed": "Failed",
    "confirm.mcpUninstall": "Uninstall this MCP plugin? This only removes the local config file.",
    "errors.browserRequest": "Browser request failed",
    "errors.browserStatus": "Browser status failed",
    "errors.cacheStatus": "Cache status failed",
    "errors.connectionTest": "Connection test failed",
    "errors.invalidApiKey": "API key is missing or invalid. Check X-API-Key in Settings.",
    "errors.invalidRequest": "Request format is invalid. Check user_id, project_id, and input.",
    "errors.documentList": "Document list failed",
    "errors.documentLoad": "Document load failed",
    "errors.memoryForget": "Forget memory failed",
    "errors.memoryRead": "Memory read failed",
    "errors.mcpMarketplace": "MCP Marketplace request failed",
    "errors.mcpPermissions": "MCP permissions request failed",
    "errors.mcpPermissionUpdate": "MCP permission update failed",
    "errors.mcpRuntimeReload": "MCP runtime reload failed",
    "errors.network": "Unable to connect to the backend service",
    "errors.ragQuery": "RAG query failed",
    "errors.ragStatus": "RAG status failed",
    "errors.requestFailed": "Request failed",
    "errors.quotaExceeded": "Today's quota has been used. Try again later or adjust backend limits.",
    "errors.send": "Send failed",
    "errors.usageStatus": "Usage status failed",
    "errors.workspaceStatus": "Workspace status failed",
    "fields.apiBaseUrl": "API Base URL",
    "fields.apiKey": "API Key",
    "fields.documentPath": "Document path",
    "fields.forgetKeyword": "Forget keyword",
    "fields.llmApiKeyConfigured": "API Key configured",
    "fields.llmBaseUrl": "LLM Base URL",
    "fields.llmModel": "LLM Model",
    "fields.llmProvider": "LLM Provider",
    "fields.reasoningMode": "Reasoning Mode",
    "fields.reasoningAutoPolicy": "Reasoning Auto Policy",
    "fields.extraBodyConfigured": "Extra Body configured",
    "fields.capabilityPreset": "Capability Preset",
    "fields.capabilityPresetRequested": "Requested Capability Preset",
    "fields.capabilityPresetEffective": "Effective Capability Preset",
    "fields.capabilityPresetSource": "Preset Source",
    "fields.supportsTools": "Supports Tools",
    "fields.supportsReasoning": "Supports Reasoning",
    "fields.requiresReasoningEcho": "Requires Reasoning Echo",
    "fields.supportsDisableReasoning": "Supports Disable Reasoning",
    "fields.supportsExtraBody": "Supports Extra Body",
    "fields.supportsJsonMode": "Supports JSON Mode",
    "fields.supportsStreaming": "Supports Streaming",
    "fields.maxContextTokens": "Max Context Tokens",
    "fields.mode": "Mode",
    "fields.query": "Query",
    "fields.temperature": "Temperature",
    "fields.timeout": "Timeout",
    "fields.url": "URL",
    "language.label": "Language",
    "language.en": "EN",
    "language.zh": "中",
    "language.ja": "日",
    "nav.browser": "Browser",
    "nav.chat": "Chat",
    "nav.documents": "Documents",
    "nav.memory": "Memory",
    "nav.mcpMarketplace": "MCP Marketplace",
    "nav.rag": "RAG",
    "nav.settings": "Settings",
    "nav.usage": "Usage",
    "nav.workspace": "Workspace",
    "panels.backendLlmConfig": "Backend LLM Config",
    "panels.browser": "Browser",
    "panels.documents": "Documents",
    "panels.memory": "Memory",
    "panels.mcpMarketplace": "MCP Marketplace",
    "panels.mcpPermissions": "MCP Permissions",
    "panels.rag": "RAG",
    "panels.settings": "API Settings",
    "panels.usageCache": "Usage / Cache",
    "panels.workspace": "Workspace",
    "settings.apiKeyPlaceholder": "Optional X-API-Key",
    "settings.hideApiKey": "Hide API key",
    "settings.showApiKey": "Show API key",
    "status.cache": "Cache",
    "status.documents": "Documents",
    "status.chunks": "Chunks",
    "status.noRecentErrors": "No recent errors.",
    "status.noWorkspace": "No workspace status yet.",
    "status.recentError": "Recent Error",
    "status.title": "Status",
    "status.usage": "Usage",
    "status.vectors": "Vectors",
    "status.workspace": "Workspace",
    "text.browserDefault": "Using Playwright Chromium by default",
    "text.browserPurpose": "Browser is for dynamic pages and fetch_url fallback, not general web search.",
    "text.browserSystem": "Using system browser channel: {channel}",
    "text.documentsHelp": "Load documents from local backend paths.",
    "text.mcpComingSoon": "This plugin is listed as coming soon and cannot be installed yet.",
    "text.mcpMarketplaceHelp": "Browse MCP catalog items and manage local MCP config entries.",
    "text.mcpNoSecrets": "Token and secret values are not displayed, entered, stored, or sent from this page.",
    "text.mcpPermissionsHelp": "Review server permissions and tool enablement. Changes only update local config.",
    "text.mcpPermissionsReloadRequired": "Permissions changed. Runtime reload was attempted automatically. Use Reload Runtime if tools are not updated.",
    "text.mcpDangerousPermissionBlocked": "Dangerous permission is blocked by policy.",
    "text.mcpNoToolsForServer": "No tools are currently discovered for this server.",
    "text.mcpReloadRequiredAfterConfigChange": "Config changed. Runtime reload was attempted automatically. Use Reload Runtime if tools are not updated.",
    "text.mcpReloadRuntimeHelp": "Reload Runtime re-reads MCP_CONFIG_PATH and MCP_CONFIG_DIR, then discovers MCP tools again.",
    "text.mcpRuntimeDisabled": "MCP Runtime is disabled.",
    "text.mcpDependencyDiagnosticsHelp": "Dependency diagnostics show what the backend process can actually execute.",
    "text.mcpDependencySuggestedFix": "Suggested fix",
    "text.mcpRuntimeReloadSuccess": "Runtime reload completed. Added {added} tools and removed {removed} tools.",
    "text.mcpRuntimeReloadRequired": "Install, enable, disable, uninstall, and permission changes attempt runtime reload automatically. Use Reload Runtime if tools are not updated.",
    "text.todayUsage": "Today: chat, rag, web_search, embedding, document_load",
    "values.no": "no",
    "values.yes": "yes",
    "values.auto": "auto",
    "values.autoDetectedFromModel": "auto detected from model",
    "values.manualOverride": "manual override",
    "values.providerDefault": "provider default",
    "values.runtimeOverride": "runtime override",
    "values.safeDefault": "safe default",
  },
  zh: {
    "app.subtitle": "前端控制台",
    "actions.cacheStatus": "缓存状态",
    "actions.cleanup": "清理",
    "actions.extractText": "提取文本",
    "actions.forget": "删除记忆",
    "actions.install": "安装",
    "actions.updatePermissions": "更新权限",
    "actions.applyPermissions": "应用权限",
    "actions.expandTools": "展开工具",
    "actions.collapseTools": "收起工具",
    "actions.list": "列表",
    "actions.listLinks": "列出链接",
    "actions.listMemory": "查看记忆",
    "actions.load": "加载",
    "actions.newChat": "新对话",
    "actions.query": "查询",
    "actions.refresh": "刷新",
    "actions.refreshStatus": "刷新状态",
    "actions.reloadRuntime": "重载运行时",
    "actions.screenshot": "截图",
    "actions.send": "发送",
    "actions.status": "状态",
    "actions.testConnection": "测试连接",
    "actions.uninstall": "卸载",
    "actions.enable": "启用",
    "actions.disable": "禁用",
    "actions.usageStatus": "用量状态",
    "common.available": "可用",
    "common.category": "分类",
    "common.comingSoon": "即将推出",
    "common.configured": "已配置",
    "common.dependencies": "依赖诊断",
    "common.disabled": "已禁用",
    "common.empty": "暂无可显示内容。",
    "common.enabled": "已启用",
    "common.environment": "环境变量",
    "common.error": "错误",
    "common.installed": "已安装",
    "common.lastReload": "上次重载",
    "common.loading": "处理中...",
    "common.notInstalled": "未安装",
    "common.official": "官方",
    "common.permissions": "权限",
    "common.permissionLevel": "权限级别",
    "common.currentPermissions": "当前权限",
    "common.availablePermissions": "可选权限",
    "common.resourceScope": "资源范围",
    "common.disabledReason": "禁用原因",
    "common.toolsEnabled": "已启用工具",
    "common.toolsDisabled": "已禁用工具",
    "common.readOnly": "读取权限",
    "common.write": "写入权限",
    "common.network": "网络",
    "common.shell": "Shell",
    "common.dangerous": "危险",
    "common.reload": "重载",
    "common.reloadCount": "重载次数",
    "common.runtime": "运行时",
    "common.servers": "服务",
    "common.success": "成功",
    "common.status": "状态",
    "common.tags": "标签",
    "common.tools": "工具",
    "chat.empty": "和你的本地 Agent API 开始一次新对话。",
    "chat.placeholder": "给 Agent 发送消息...",
    "chat.thinking": "思考中...",
    "common.unknown": "未知",
    "common.version": "版本",
    "common.failed": "失败",
    "confirm.mcpUninstall": "确认卸载这个 MCP 插件吗？这只会删除本地配置文件。",
    "errors.browserRequest": "浏览器请求失败",
    "errors.browserStatus": "读取浏览器状态失败",
    "errors.cacheStatus": "读取缓存状态失败",
    "errors.connectionTest": "连接测试失败",
    "errors.invalidApiKey": "API Key 缺失或错误，请在设置中检查 X-API-Key。",
    "errors.invalidRequest": "请求格式错误，请检查 user_id、project_id 和输入内容。",
    "errors.documentList": "读取文档列表失败",
    "errors.documentLoad": "加载文档失败",
    "errors.memoryForget": "删除记忆失败",
    "errors.memoryRead": "读取记忆失败",
    "errors.mcpMarketplace": "MCP 插件市场请求失败",
    "errors.mcpPermissions": "MCP 权限请求失败",
    "errors.mcpPermissionUpdate": "MCP 权限更新失败",
    "errors.mcpRuntimeReload": "MCP 运行时重载失败",
    "errors.network": "无法连接后端服务",
    "errors.ragQuery": "RAG 查询失败",
    "errors.ragStatus": "读取 RAG 状态失败",
    "errors.requestFailed": "请求失败",
    "errors.quotaExceeded": "今日额度已用完，请稍后再试或调整后端限额。",
    "errors.send": "发送失败",
    "errors.usageStatus": "读取用量状态失败",
    "errors.workspaceStatus": "读取工作区状态失败",
    "fields.apiBaseUrl": "API 基础地址",
    "fields.apiKey": "API Key",
    "fields.documentPath": "文档路径",
    "fields.forgetKeyword": "删除关键词",
    "fields.llmApiKeyConfigured": "API Key 已配置",
    "fields.llmBaseUrl": "LLM 基础地址",
    "fields.llmModel": "LLM 模型",
    "fields.llmProvider": "LLM Provider",
    "fields.reasoningMode": "推理模式",
    "fields.reasoningAutoPolicy": "推理自动策略",
    "fields.extraBodyConfigured": "Extra Body 已配置",
    "fields.capabilityPreset": "能力预设",
    "fields.capabilityPresetRequested": "请求的能力预设",
    "fields.capabilityPresetEffective": "实际能力预设",
    "fields.capabilityPresetSource": "预设来源",
    "fields.supportsTools": "支持工具",
    "fields.supportsReasoning": "支持推理",
    "fields.requiresReasoningEcho": "需要回传推理内容",
    "fields.supportsDisableReasoning": "支持关闭推理",
    "fields.supportsExtraBody": "支持 Extra Body",
    "fields.supportsJsonMode": "支持 JSON 模式",
    "fields.supportsStreaming": "支持流式输出",
    "fields.maxContextTokens": "最大上下文长度",
    "fields.mode": "模式",
    "fields.query": "查询",
    "fields.temperature": "温度",
    "fields.timeout": "超时",
    "fields.url": "URL",
    "language.label": "语言",
    "language.en": "英",
    "language.zh": "中",
    "language.ja": "日",
    "nav.browser": "浏览器",
    "nav.chat": "对话",
    "nav.documents": "文档",
    "nav.memory": "记忆",
    "nav.mcpMarketplace": "MCP 插件市场",
    "nav.rag": "RAG",
    "nav.settings": "设置",
    "nav.usage": "用量",
    "nav.workspace": "工作区",
    "panels.backendLlmConfig": "后端 LLM 配置",
    "panels.browser": "浏览器",
    "panels.documents": "文档",
    "panels.memory": "记忆",
    "panels.mcpMarketplace": "MCP 插件市场",
    "panels.mcpPermissions": "MCP 权限",
    "panels.rag": "RAG",
    "panels.settings": "API 设置",
    "panels.usageCache": "用量 / 缓存",
    "panels.workspace": "工作区",
    "settings.apiKeyPlaceholder": "可选 X-API-Key",
    "settings.hideApiKey": "隐藏 API Key",
    "settings.showApiKey": "显示 API Key",
    "status.cache": "缓存",
    "status.documents": "文档",
    "status.chunks": "分块",
    "status.noRecentErrors": "暂无近期错误。",
    "status.noWorkspace": "暂无工作区状态。",
    "status.recentError": "最近错误",
    "status.title": "状态",
    "status.usage": "用量",
    "status.vectors": "向量",
    "status.workspace": "工作区",
    "text.browserDefault": "默认使用 Playwright Chromium",
    "text.browserPurpose": "浏览器用于动态页面和 fetch_url 兜底，不用于通用网页搜索。",
    "text.browserSystem": "正在使用系统浏览器通道：{channel}",
    "text.documentsHelp": "从后端本地路径加载文档。",
    "text.mcpComingSoon": "该插件目前只是占位展示，暂不支持安装。",
    "text.mcpMarketplaceHelp": "查看 MCP 插件目录，并管理本机 MCP 配置。",
    "text.mcpNoSecrets": "本页面不显示、不输入、不保存、也不发送 token 或 secret 值。",
    "text.mcpPermissionsHelp": "查看服务权限和工具启用状态。修改只会更新本地配置。",
    "text.mcpPermissionsReloadRequired": "权限已改变，系统已自动尝试重载运行时。如工具未更新，请手动点击重载运行时。",
    "text.mcpDangerousPermissionBlocked": "危险权限已被策略禁止。",
    "text.mcpNoToolsForServer": "当前服务还没有发现工具。",
    "text.mcpReloadRequiredAfterConfigChange": "配置已改变，系统已自动尝试重载运行时。如工具未更新，请手动点击重载运行时。",
    "text.mcpReloadRuntimeHelp": "重载运行时会重新读取 MCP_CONFIG_PATH 和 MCP_CONFIG_DIR，并重新发现 MCP tools。",
    "text.mcpRuntimeDisabled": "MCP Runtime 当前未启用。",
    "text.mcpDependencyDiagnosticsHelp": "依赖诊断展示后端进程实际能执行哪些命令。",
    "text.mcpDependencySuggestedFix": "建议修复",
    "text.mcpRuntimeReloadSuccess": "运行时重载完成。新增 {added} 个工具，移除 {removed} 个工具。",
    "text.mcpRuntimeReloadRequired": "安装、启用、禁用、卸载和权限变更会自动尝试重载运行时。如工具未更新，请手动点击重载运行时。",
    "text.todayUsage": "今日：chat、rag、web_search、embedding、document_load",
    "values.no": "否",
    "values.yes": "是",
    "values.auto": "自动",
    "values.autoDetectedFromModel": "根据模型自动识别",
    "values.manualOverride": "手动覆盖",
    "values.providerDefault": "Provider 默认",
    "values.runtimeOverride": "运行时覆盖",
    "values.safeDefault": "安全默认",
  },
  ja: {
    "app.subtitle": "フロントエンドコンソール",
    "actions.cacheStatus": "キャッシュ状態",
    "actions.cleanup": "クリーンアップ",
    "actions.extractText": "テキスト抽出",
    "actions.forget": "記憶を削除",
    "actions.install": "インストール",
    "actions.updatePermissions": "権限を更新",
    "actions.applyPermissions": "権限を適用",
    "actions.expandTools": "ツールを展開",
    "actions.collapseTools": "ツールを折りたたむ",
    "actions.list": "一覧",
    "actions.listLinks": "リンク一覧",
    "actions.listMemory": "記憶一覧",
    "actions.load": "読み込み",
    "actions.newChat": "新しい会話",
    "actions.query": "検索",
    "actions.refresh": "更新",
    "actions.refreshStatus": "状態を更新",
    "actions.reloadRuntime": "ランタイム再読み込み",
    "actions.screenshot": "スクリーンショット",
    "actions.send": "送信",
    "actions.status": "状態",
    "actions.testConnection": "接続テスト",
    "actions.uninstall": "アンインストール",
    "actions.enable": "有効化",
    "actions.disable": "無効化",
    "actions.usageStatus": "利用状況",
    "common.available": "利用可能",
    "common.category": "カテゴリ",
    "common.comingSoon": "近日対応",
    "common.configured": "設定済み",
    "common.dependencies": "依存関係診断",
    "common.disabled": "無効",
    "common.empty": "表示する項目はありません。",
    "common.enabled": "有効",
    "common.environment": "環境変数",
    "common.error": "エラー",
    "common.installed": "インストール済み",
    "common.lastReload": "前回の再読み込み",
    "common.loading": "処理中...",
    "common.notInstalled": "未インストール",
    "common.official": "公式",
    "common.permissions": "権限",
    "common.permissionLevel": "権限レベル",
    "common.currentPermissions": "現在の権限",
    "common.availablePermissions": "利用可能な権限",
    "common.resourceScope": "リソース範囲",
    "common.disabledReason": "無効理由",
    "common.toolsEnabled": "有効なツール",
    "common.toolsDisabled": "無効なツール",
    "common.readOnly": "読み取り権限",
    "common.write": "書き込み権限",
    "common.network": "ネットワーク",
    "common.shell": "Shell",
    "common.dangerous": "危険",
    "common.reload": "再読み込み",
    "common.reloadCount": "再読み込み回数",
    "common.runtime": "ランタイム",
    "common.servers": "サーバー",
    "common.success": "成功",
    "common.status": "状態",
    "common.tags": "タグ",
    "common.tools": "ツール",
    "chat.empty": "ローカル Agent API と新しい会話を始めます。",
    "chat.placeholder": "Agent にメッセージ...",
    "chat.thinking": "考え中...",
    "common.unknown": "不明",
    "common.version": "バージョン",
    "common.failed": "失敗",
    "confirm.mcpUninstall": "この MCP プラグインをアンインストールしますか？ローカル設定ファイルだけを削除します。",
    "errors.browserRequest": "ブラウザーリクエストに失敗しました",
    "errors.browserStatus": "ブラウザー状態の取得に失敗しました",
    "errors.cacheStatus": "キャッシュ状態の取得に失敗しました",
    "errors.connectionTest": "接続テストに失敗しました",
    "errors.invalidApiKey": "API Key がないか無効です。設定の X-API-Key を確認してください。",
    "errors.invalidRequest": "リクエスト形式が無効です。user_id、project_id、入力内容を確認してください。",
    "errors.documentList": "文書一覧の取得に失敗しました",
    "errors.documentLoad": "文書の読み込みに失敗しました",
    "errors.memoryForget": "記憶の削除に失敗しました",
    "errors.memoryRead": "記憶の取得に失敗しました",
    "errors.mcpMarketplace": "MCP マーケットプレイスのリクエストに失敗しました",
    "errors.mcpPermissions": "MCP 権限リクエストに失敗しました",
    "errors.mcpPermissionUpdate": "MCP 権限更新に失敗しました",
    "errors.mcpRuntimeReload": "MCP ランタイムの再読み込みに失敗しました",
    "errors.network": "バックエンドサービスに接続できません",
    "errors.ragQuery": "RAG 検索に失敗しました",
    "errors.ragStatus": "RAG 状態の取得に失敗しました",
    "errors.requestFailed": "リクエストに失敗しました",
    "errors.quotaExceeded": "本日の上限に達しました。後でもう一度試すか、バックエンドの制限を調整してください。",
    "errors.send": "送信に失敗しました",
    "errors.usageStatus": "利用状況の取得に失敗しました",
    "errors.workspaceStatus": "ワークスペース状態の取得に失敗しました",
    "fields.apiBaseUrl": "API ベース URL",
    "fields.apiKey": "API Key",
    "fields.documentPath": "文書パス",
    "fields.forgetKeyword": "削除キーワード",
    "fields.llmApiKeyConfigured": "API Key 設定済み",
    "fields.llmBaseUrl": "LLM ベース URL",
    "fields.llmModel": "LLM モデル",
    "fields.llmProvider": "LLM Provider",
    "fields.reasoningMode": "推論モード",
    "fields.reasoningAutoPolicy": "推論自動ポリシー",
    "fields.extraBodyConfigured": "Extra Body 設定済み",
    "fields.capabilityPreset": "能力プリセット",
    "fields.capabilityPresetRequested": "要求された能力プリセット",
    "fields.capabilityPresetEffective": "有効な能力プリセット",
    "fields.capabilityPresetSource": "プリセット元",
    "fields.supportsTools": "ツール対応",
    "fields.supportsReasoning": "推論対応",
    "fields.requiresReasoningEcho": "推論内容の返送が必要",
    "fields.supportsDisableReasoning": "推論無効化に対応",
    "fields.supportsExtraBody": "Extra Body 対応",
    "fields.supportsJsonMode": "JSON モード対応",
    "fields.supportsStreaming": "ストリーミング対応",
    "fields.maxContextTokens": "最大コンテキスト長",
    "fields.mode": "モード",
    "fields.query": "検索",
    "fields.temperature": "温度",
    "fields.timeout": "タイムアウト",
    "fields.url": "URL",
    "language.label": "言語",
    "language.en": "英",
    "language.zh": "中",
    "language.ja": "日",
    "nav.browser": "ブラウザー",
    "nav.chat": "チャット",
    "nav.documents": "文書",
    "nav.memory": "記憶",
    "nav.mcpMarketplace": "MCP マーケット",
    "nav.rag": "RAG",
    "nav.settings": "設定",
    "nav.usage": "利用状況",
    "nav.workspace": "ワークスペース",
    "panels.backendLlmConfig": "バックエンド LLM 設定",
    "panels.browser": "ブラウザー",
    "panels.documents": "文書",
    "panels.memory": "記憶",
    "panels.mcpMarketplace": "MCP マーケットプレイス",
    "panels.mcpPermissions": "MCP 権限",
    "panels.rag": "RAG",
    "panels.settings": "API 設定",
    "panels.usageCache": "利用状況 / キャッシュ",
    "panels.workspace": "ワークスペース",
    "settings.apiKeyPlaceholder": "任意の X-API-Key",
    "settings.hideApiKey": "API Key を隠す",
    "settings.showApiKey": "API Key を表示",
    "status.cache": "キャッシュ",
    "status.documents": "文書",
    "status.chunks": "チャンク",
    "status.noRecentErrors": "最近のエラーはありません。",
    "status.noWorkspace": "ワークスペース状態はまだありません。",
    "status.recentError": "最近のエラー",
    "status.title": "状態",
    "status.usage": "利用状況",
    "status.vectors": "ベクトル",
    "status.workspace": "ワークスペース",
    "text.browserDefault": "既定では Playwright Chromium を使用します",
    "text.browserPurpose": "ブラウザーは動的ページと fetch_url のフォールバック用で、一般的な Web 検索用ではありません。",
    "text.browserSystem": "システムブラウザーチャネルを使用中：{channel}",
    "text.documentsHelp": "バックエンドのローカルパスから文書を読み込みます。",
    "text.mcpComingSoon": "このプラグインは近日対応のため、まだインストールできません。",
    "text.mcpMarketplaceHelp": "MCP カタログを確認し、ローカル MCP 設定を管理します。",
    "text.mcpNoSecrets": "このページでは token や secret 値を表示、入力、保存、送信しません。",
    "text.mcpPermissionsHelp": "サーバー権限とツールの有効状態を確認します。変更はローカル設定だけを更新します。",
    "text.mcpPermissionsReloadRequired": "権限が変更され、ランタイムの自動再読み込みを試行しました。ツールが更新されない場合は手動で再読み込みしてください。",
    "text.mcpDangerousPermissionBlocked": "危険な権限はポリシーで禁止されています。",
    "text.mcpNoToolsForServer": "このサーバーで検出されたツールはまだありません。",
    "text.mcpReloadRequiredAfterConfigChange": "設定が変更され、ランタイムの自動再読み込みを試行しました。ツールが更新されない場合は手動で再読み込みしてください。",
    "text.mcpReloadRuntimeHelp": "ランタイム再読み込みは MCP_CONFIG_PATH と MCP_CONFIG_DIR を読み直し、MCP tools を再検出します。",
    "text.mcpRuntimeDisabled": "MCP Runtime は無効です。",
    "text.mcpDependencyDiagnosticsHelp": "依存関係診断はバックエンドプロセスが実際に実行できるコマンドを表示します。",
    "text.mcpDependencySuggestedFix": "修正案",
    "text.mcpRuntimeReloadSuccess": "ランタイム再読み込みが完了しました。追加 {added} 件、削除 {removed} 件。",
    "text.mcpRuntimeReloadRequired": "インストール、有効化、無効化、アンインストール、権限変更ではランタイムの自動再読み込みを試行します。ツールが更新されない場合は手動で再読み込みしてください。",
    "text.todayUsage": "本日：chat、rag、web_search、embedding、document_load",
    "values.no": "いいえ",
    "values.yes": "はい",
    "values.auto": "自動",
    "values.autoDetectedFromModel": "モデルから自動検出",
    "values.manualOverride": "手動上書き",
    "values.providerDefault": "Provider 既定",
    "values.runtimeOverride": "ランタイム上書き",
    "values.safeDefault": "安全な既定値",
  },
};

const languageNames: Record<Language, string> = {
  en: "English",
  zh: "中文",
  ja: "日本語",
};

const I18nContext = createContext<{
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: TranslationKey, values?: Record<string, string | number>) => string;
  languageNames: Record<Language, string>;
} | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => {
    const stored = localStorage.getItem("agent_language");
    return stored === "zh" || stored === "ja" || stored === "en" ? stored : "zh";
  });

  useEffect(() => {
    localStorage.setItem("agent_language", language);
    document.documentElement.lang = language === "zh" ? "zh-CN" : language;
  }, [language]);

  const value = useMemo(
    () => ({
      language,
      setLanguage: setLanguageState,
      languageNames,
      t: (key: TranslationKey, values?: Record<string, string | number>) => {
        let text = translations[language][key] ?? translations.en[key] ?? key;
        for (const [name, value] of Object.entries(values ?? {})) {
          text = text.replace(`{${name}}`, String(value));
        }
        return text;
      },
    }),
    [language],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used inside I18nProvider");
  }
  return context;
}
