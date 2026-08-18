import { ChevronDown, ChevronRight, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import type { MCPPermissionServer, MCPPermissionsResponse } from "../types/api";
import { buttonClass } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  onError: (message: string) => void;
  onChanged: (message: string) => void;
}

export default function McpPermissionsSection({ api, onError, onChanged }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [permissions, setPermissions] = useState<MCPPermissionsResponse | null>(null);
  const [expandedServers, setExpandedServers] = useState<Record<string, boolean>>({});
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  const [drafts, setDrafts] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);
  const [workingServer, setWorkingServer] = useState("");

  async function refresh() {
    setLoading(true);
    try {
      const response = await api.mcpPermissions();
      const data = response.data ?? {};
      setPermissions(data);
      setDrafts(Object.fromEntries((data.servers ?? []).map((server) => [server.server_name, server.permissions ?? ["read_only"]])));
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.mcpPermissions"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open && !permissions && !loading) {
      void refresh();
    }
  }, [open, permissions, loading]);

  async function applyPermissions(server: MCPPermissionServer) {
    const next = drafts[server.server_name] ?? [];
    setWorkingServer(server.server_name);
    try {
      await api.updateMcpPermissions(server.server_name, { permissions: next });
      onChanged(t("text.mcpPermissionsReloadRequired"));
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.mcpPermissionUpdate"));
    } finally {
      setWorkingServer("");
    }
  }

  return (
    <details className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
        <span>
          <span className="flex items-center gap-2 text-sm font-semibold text-neutral-100">
            <ShieldCheck size={16} />
            {t("panels.mcpPermissions")}
          </span>
          <span className="mt-1 block text-sm text-neutral-400">{t("text.mcpPermissionsHelp")}</span>
        </span>
        <span className="text-sm text-neutral-500">{summaryText(permissions)}</span>
      </summary>

      <div className="mt-4 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={loading} onClick={refresh}>
            <RefreshCw size={16} />
            {loading ? t("common.loading") : t("actions.refresh")}
          </button>
        </div>

        {permissions?.summary ? (
          <div className="grid gap-3 sm:grid-cols-4">
            <Metric label={t("common.servers")} value={numberValue(permissions.summary.servers_total)} />
            <Metric label={t("common.toolsEnabled")} value={numberValue(permissions.summary.tools_enabled)} />
            <Metric label={t("common.toolsDisabled")} value={numberValue(permissions.summary.tools_disabled)} />
            <Metric label={t("common.write")} value={numberValue(permissions.summary.permission_counts?.write)} />
          </div>
        ) : null}

        {(permissions?.servers ?? []).map((server) => {
          const serverOpen = Boolean(expandedServers[server.server_name]);
          const toolsOpen = Boolean(expandedTools[server.server_name]);
          const draft = drafts[server.server_name] ?? server.permissions ?? ["read_only"];
          return (
            <div key={server.server_name} className="rounded-xl border border-neutral-800 bg-neutral-950 p-3">
              <button
                className="flex w-full items-center justify-between gap-3 text-left"
                onClick={() => setExpandedServers((current) => ({ ...current, [server.server_name]: !serverOpen }))}
              >
                <span>
                  <span className="font-mono text-sm text-neutral-100">{server.server_name}</span>
                  <span className="ml-2 text-xs text-neutral-500">
                    {server.tools_enabled ?? 0}/{server.tools_total ?? 0} {t("common.tools")}
                  </span>
                </span>
                {serverOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </button>

              {serverOpen ? (
                <div className="mt-4 space-y-4">
                  <p className="text-sm text-neutral-400">{server.resource_scope?.description}</p>
                  {server.url_preview ? <p className="font-mono text-xs text-neutral-500">{server.url_preview}</p> : null}
                  {server.header_keys?.length ? <p className="text-xs text-neutral-500">headers: {server.header_keys.join(", ")}</p> : null}
                  <div className="flex flex-wrap gap-2">
                    {(server.available_permissions ?? ["read_only"]).map((permission) => (
                      <label key={permission} className="inline-flex items-center gap-2 rounded-xl border border-neutral-800 px-3 py-2 text-sm text-neutral-300">
                        <input
                          type="checkbox"
                          disabled={permission === "dangerous" || workingServer === server.server_name}
                          checked={draft.includes(permission)}
                          onChange={(event) => {
                            setDrafts((current) => ({
                              ...current,
                              [server.server_name]: event.target.checked
                                ? Array.from(new Set([...draft, permission]))
                                : draft.filter((item) => item !== permission),
                            }));
                          }}
                        />
                        {permissionLabel(permission, t)}
                      </label>
                    ))}
                  </div>
                  <button className={buttonClass} disabled={workingServer === server.server_name || draft.length === 0} onClick={() => applyPermissions(server)}>
                    {workingServer === server.server_name ? t("common.loading") : t("actions.applyPermissions")}
                  </button>
                  <button
                    className="block text-sm text-neutral-300 underline-offset-4 hover:underline"
                    onClick={() => setExpandedTools((current) => ({ ...current, [server.server_name]: !toolsOpen }))}
                  >
                    {toolsOpen ? t("actions.collapseTools") : t("actions.expandTools")}
                  </button>
                  {toolsOpen ? <ToolList server={server} /> : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </details>
  );
}

function ToolList({ server }: { server: MCPPermissionServer }) {
  const { t } = useI18n();
  if (!server.tools?.length) {
    return <p className="text-sm text-neutral-500">{t("text.mcpNoToolsForServer")}</p>;
  }
  return (
    <div className="max-h-96 overflow-auto rounded-xl border border-neutral-800">
      {server.tools.map((tool) => (
        <div key={tool.qualified_name ?? tool.name} className="border-b border-neutral-800 p-3 last:border-b-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-mono text-xs text-neutral-100">{tool.name}</p>
            <span className={tool.enabled ? "text-xs text-emerald-300" : "text-xs text-amber-300"}>{tool.enabled ? t("common.enabled") : t("common.disabled")}</span>
          </div>
          <p className="mt-1 text-xs text-neutral-500">{permissionLabel(tool.permission_level ?? "", t)}</p>
          {tool.disabled_reason ? <p className="mt-2 text-xs text-amber-200">{tool.disabled_reason}</p> : null}
        </div>
      ))}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-neutral-800 bg-black/40 p-3">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-neutral-100">{value}</p>
    </div>
  );
}

function summaryText(data: MCPPermissionsResponse | null) {
  if (!data?.summary) {
    return "";
  }
  return `${data.summary.tools_enabled ?? 0}/${data.summary.tools_total ?? 0}`;
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function permissionLabel(permission: string, t: ReturnType<typeof useI18n>["t"]) {
  if (permission === "read_only") return t("common.readOnly");
  if (permission === "write") return t("common.write");
  if (permission === "network") return t("common.network");
  if (permission === "shell") return t("common.shell");
  if (permission === "dangerous") return t("common.dangerous");
  return permission;
}
