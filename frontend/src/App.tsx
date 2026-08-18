import { useMemo, useState } from "react";
import { createApiClient } from "./api/client";
import BrowserPanel from "./components/BrowserPanel";
import ChatWindow from "./components/ChatWindow";
import DocumentPanel from "./components/DocumentPanel";
import MemoryPanel from "./components/MemoryPanel";
import McpMarketplacePanel from "./components/McpMarketplacePanel";
import RagPanel from "./components/RagPanel";
import SettingsPanel from "./components/SettingsPanel";
import Sidebar, { type PanelName } from "./components/Sidebar";
import StatusPanel from "./components/StatusPanel";
import UsagePanel from "./components/UsagePanel";
import WorkspacePanel from "./components/WorkspacePanel";
import { useLocalStorage } from "./hooks/useLocalStorage";
import { useI18n } from "./i18n";
import type { ChatMessage, WorkspaceStatusResponse } from "./types/api";

function createMessage(role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    content,
    createdAt: new Date().toISOString(),
  };
}

export default function App() {
  const { t } = useI18n();
  const [apiBaseUrl, setApiBaseUrl] = useLocalStorage("agent_api_base_url", "http://127.0.0.1:8000");
  const [apiKey, setApiKey] = useLocalStorage("agent_api_key", "");
  const [userId, setUserId] = useLocalStorage("agent_user_id", "default_user");
  const [projectId, setProjectId] = useLocalStorage("agent_project_id", "default_project");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [workspaceStatus, setWorkspaceStatus] = useState<WorkspaceStatusResponse | null>(null);
  const [ragStatus, setRagStatus] = useState<unknown>(null);
  const [usageStatus, setUsageStatus] = useState<unknown>(null);
  const [cacheStatus, setCacheStatus] = useState<unknown>(null);
  const [browserStatus, setBrowserStatus] = useState<unknown>(null);
  const [selectedPanel, setSelectedPanel] = useState<PanelName>("chat");
  const [lastError, setLastError] = useState("");

  const api = useMemo(
    () =>
      createApiClient({
        baseUrl: apiBaseUrl,
        apiKey,
        userId,
        projectId,
        messages: {
          networkError: t("errors.network"),
          requestFailed: t("errors.requestFailed"),
          invalidApiKey: t("errors.invalidApiKey"),
          quotaExceeded: t("errors.quotaExceeded"),
          invalidRequest: t("errors.invalidRequest"),
        },
      }),
    [apiBaseUrl, apiKey, userId, projectId, t],
  );

  function recordError(message: string) {
    setLastError(message);
    setMessages((current) => [...current, createMessage("error", message)]);
  }

  async function sendMessage(content: string) {
    const userMessage = createMessage("user", content);
    setMessages((current) => [...current, userMessage]);
    setLoading(true);
    try {
      const response = await api.chat({ message: content });
      setMessages((current) => [...current, createMessage("assistant", response.answer || "(empty response)")]);
      setLastError("");
    } catch (error) {
      recordError(error instanceof Error ? error.message : t("errors.send"));
    } finally {
      setLoading(false);
    }
  }

  function renderMainPanel() {
    if (selectedPanel === "chat") {
      return <ChatWindow messages={messages} loading={loading} userId={userId} projectId={projectId} apiBaseUrl={apiBaseUrl} onSend={sendMessage} />;
    }
    if (selectedPanel === "workspace") {
      return <WorkspacePanel api={api} status={workspaceStatus} setStatus={setWorkspaceStatus} onError={recordError} />;
    }
    if (selectedPanel === "documents") {
      return <DocumentPanel api={api} onError={recordError} />;
    }
    if (selectedPanel === "memory") {
      return <MemoryPanel api={api} onError={recordError} />;
    }
    if (selectedPanel === "rag") {
      return <RagPanel api={api} status={ragStatus} setStatus={setRagStatus} onError={recordError} />;
    }
    if (selectedPanel === "usage") {
      return (
        <UsagePanel
          api={api}
          usageStatus={usageStatus}
          cacheStatus={cacheStatus}
          setUsageStatus={setUsageStatus}
          setCacheStatus={setCacheStatus}
          onError={recordError}
        />
      );
    }
    if (selectedPanel === "browser") {
      return <BrowserPanel api={api} status={browserStatus} setStatus={setBrowserStatus} onError={recordError} />;
    }
    if (selectedPanel === "mcp") {
      return <McpMarketplacePanel api={api} onError={recordError} />;
    }
    return (
      <SettingsPanel
        apiBaseUrl={apiBaseUrl}
        apiKey={apiKey}
        userId={userId}
        projectId={projectId}
        setApiBaseUrl={setApiBaseUrl}
        setApiKey={setApiKey}
        setUserId={setUserId}
        setProjectId={setProjectId}
        api={api}
        onError={recordError}
      />
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-neutral-950 text-neutral-100">
      <Sidebar
        selectedPanel={selectedPanel}
        userId={userId}
        projectId={projectId}
        onSelect={setSelectedPanel}
        onNewChat={() => {
          setMessages([]);
          setSelectedPanel("chat");
        }}
      />
      {renderMainPanel()}
      <StatusPanel workspaceStatus={workspaceStatus} ragStatus={ragStatus} usageStatus={usageStatus} lastError={lastError} />
    </div>
  );
}
