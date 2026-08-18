import { CheckCircle2, CircleOff, Download, Power, PowerOff, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import type {
  MCPDependencyStatus,
  MCPInstalledItem,
  MCPMarketplaceItem,
  MCPMarketplaceResponse,
  MCPRuntimeDependenciesResponse,
  MCPRuntimeStatusResponse,
} from "../types/api";
import McpPermissionsSection from "./McpPermissionsSection";
import { buttonClass, JsonBlock, PanelShell } from "./PanelHelpers";

type Filter = "all" | "available" | "installed" | "coming_soon";

interface Props {
  api: ReturnType<typeof createApiClient>;
  onError: (message: string) => void;
}

export default function McpMarketplacePanel({ api, onError }: Props) {
  const { t } = useI18n();
  const [catalog, setCatalog] = useState<MCPMarketplaceResponse | null>(null);
  const [installed, setInstalled] = useState<MCPInstalledItem[]>([]);
  const [runtimeStatus, setRuntimeStatus] = useState<MCPRuntimeStatusResponse | null>(null);
  const [dependencies, setDependencies] = useState<MCPRuntimeDependenciesResponse | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [workingKey, setWorkingKey] = useState("");
  const [panelError, setPanelError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    setLoading(true);
    setPanelError("");
    try {
      const [marketplaceResponse, installedResponse, runtimeResponse, dependenciesResponse] = await Promise.all([
        api.mcpMarketplace(),
        api.mcpInstalled(),
        api.mcpRuntimeStatus(),
        api.mcpRuntimeDependencies(),
      ]);
      setCatalog(marketplaceResponse.data ?? { items: [] });
      setInstalled(installedResponse.data?.items ?? marketplaceResponse.data?.installed ?? []);
      setRuntimeStatus(runtimeResponse.data ?? null);
      setDependencies(dependenciesResponse.data ?? null);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("errors.mcpMarketplace");
      setPanelError(message);
      onError(message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [api]);

  const installedByServer = useMemo(() => {
    return new Map(installed.map((item) => [item.server_name, item]));
  }, [installed]);

  const items = useMemo(() => {
    const catalogItems = catalog?.items ?? [];
    const catalogServers = new Set(catalogItems.map((item) => item.server_name));
    const installedOnly: MCPMarketplaceItem[] = installed
      .filter((item) => !catalogServers.has(item.server_name))
      .map((item) => ({
        id: item.server_name,
        server_name: item.server_name,
        display_name: item.server_name,
        category: item.source ?? "local",
        status: "available",
        installed: true,
        installed_enabled: Boolean(item.enabled),
        installable: false,
        permissions: { default: item.permissions ?? [], available: item.permissions ?? [] },
        env_keys: item.env_keys ?? [],
        url_preview: item.url_preview,
        header_keys: item.header_keys ?? [],
      }));
    return [...catalogItems, ...installedOnly];
  }, [catalog?.items, installed]);

  const visibleItems = items.filter((item) => {
    const installedItem = installedByServer.get(item.server_name);
    const isInstalled = item.installed || Boolean(installedItem);
    if (filter === "available") {
      return item.status === "available" && !isInstalled;
    }
    if (filter === "installed") {
      return isInstalled;
    }
    if (filter === "coming_soon") {
      return item.status === "coming_soon";
    }
    return true;
  });

  const availableCount = catalog?.counts?.available ?? items.filter((item) => item.status === "available").length;
  const installedCount = catalog?.counts?.installed ?? installed.length;
  const catalogTotal = catalog?.counts?.catalog_total ?? catalog?.items.length ?? 0;

  async function runAction(key: string, action: () => Promise<unknown>) {
    setWorkingKey(key);
    setPanelError("");
    setNotice("");
    try {
      const response = await action();
      setNotice(t("text.mcpReloadRequiredAfterConfigChange"));
      if (response && typeof response === "object" && "data" in response) {
        const data = (response as { data?: { runtime_reload_required?: boolean; hot_reload?: boolean } | null }).data;
        if (data?.runtime_reload_required || data?.hot_reload === true) {
          setNotice(t("text.mcpReloadRequiredAfterConfigChange"));
        }
      }
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : t("errors.mcpMarketplace");
      setPanelError(message);
      onError(message);
    } finally {
      setWorkingKey("");
    }
  }

  function install(item: MCPMarketplaceItem) {
    const itemId = item.id ?? item.server_name;
    void runAction(`install:${item.server_name}`, () => api.installMcp(itemId, { enabled: true, permissions: null }));
  }

  function enable(serverName: string) {
    void runAction(`enable:${serverName}`, () => api.enableMcp(serverName));
  }

  function disable(serverName: string) {
    void runAction(`disable:${serverName}`, () => api.disableMcp(serverName));
  }

  function uninstall(serverName: string) {
    if (!window.confirm(t("confirm.mcpUninstall"))) {
      return;
    }
    void runAction(`uninstall:${serverName}`, () => api.uninstallMcp(serverName));
  }

  async function reloadRuntime() {
    setWorkingKey("runtime:reload");
    setPanelError("");
    setNotice("");
    try {
      const response = await api.reloadMcpRuntime();
      const diff = response.data?.diff ?? {};
      setNotice(
        t("text.mcpRuntimeReloadSuccess", {
          added: diff.added_tools?.length ?? 0,
          removed: diff.removed_tools?.length ?? 0,
        }),
      );
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : t("errors.mcpRuntimeReload");
      setPanelError(message);
      onError(message);
    } finally {
      setWorkingKey("");
    }
  }

  return (
    <PanelShell title={t("panels.mcpMarketplace")}>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-neutral-400">{t("text.mcpMarketplaceHelp")}</p>
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={loading || Boolean(workingKey)} onClick={refresh}>
            <RefreshCw size={16} />
            {loading ? t("common.loading") : t("actions.refresh")}
          </button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Metric label="Catalog" value={catalogTotal} />
          <Metric label={t("common.available")} value={availableCount} />
          <Metric label={t("common.installed")} value={installedCount} />
        </div>
        <div className="mt-4 rounded-xl border border-amber-900/60 bg-amber-950/30 p-3 text-sm text-amber-200">
          {t("text.mcpRuntimeReloadRequired")}
        </div>
        <p className="mt-3 text-xs text-neutral-500">{t("text.mcpNoSecrets")}</p>
      </div>

      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-neutral-100">{t("common.runtime")}</h3>
            <p className="mt-1 text-sm text-neutral-400">
              {runtimeStatus?.runtime?.enabled ? t("text.mcpReloadRuntimeHelp") : t("text.mcpRuntimeDisabled")}
            </p>
          </div>
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={Boolean(workingKey)} onClick={reloadRuntime}>
            <RefreshCw size={16} />
            {workingKey === "runtime:reload" ? t("common.loading") : t("actions.reloadRuntime")}
          </button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-4">
          <Metric label={t("common.servers")} value={numberValue(runtimeStatus?.runtime?.servers_enabled)} />
          <Metric label={t("common.tools")} value={numberValue(runtimeStatus?.runtime?.tools_enabled)} />
          <Metric label={t("common.reloadCount")} value={numberValue(runtimeStatus?.runtime?.reload_count)} />
          <Metric label={t("common.disabled")} value={numberValue(runtimeStatus?.runtime?.tools_disabled)} />
        </div>
        <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <Detail label={t("common.lastReload")} value={stringValue(runtimeStatus?.runtime?.last_reload_at)} />
          <Detail
            label={t("common.status")}
            value={
              runtimeStatus?.runtime?.last_reload_success === undefined
                ? "-"
                : runtimeStatus.runtime.last_reload_success
                  ? t("common.success")
                  : t("common.failed")
            }
          />
          <Detail label="tools_total" value={String(numberValue(runtimeStatus?.runtime?.tools_total))} />
          <Detail label="last_reload_error" value={stringValue(runtimeStatus?.runtime?.last_reload_error)} />
        </div>
      </div>

      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <div>
          <h3 className="text-sm font-semibold text-neutral-100">{t("common.dependencies")}</h3>
          <p className="mt-1 text-sm text-neutral-400">{t("text.mcpDependencyDiagnosticsHelp")}</p>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <DependencyCard name="Docker" status={dependencies?.docker} />
          <DependencyCard name="Node" status={dependencies?.node} />
          <DependencyCard name="npm" status={dependencies?.npm} />
          <DependencyCard name="npx" status={dependencies?.npx} />
        </div>
        <div className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
          {["MCP_DOCKER_BIN", "MCP_NPX_BIN", "MCP_FILESYSTEM_ROOT"].map((key) => {
            const item = dependencies?.env?.[key] ?? {};
            return <Detail key={key} label={key} value={`${t("common.configured")}: ${item.configured ? t("values.yes") : t("values.no")}`} />;
          })}
        </div>
        <ServerSuggestedFixes diagnostics={runtimeStatus?.diagnostics} title={t("text.mcpDependencySuggestedFix")} />
      </div>

      <div className="flex flex-wrap gap-2">
        {(["all", "available", "installed", "coming_soon"] as Filter[]).map((value) => (
          <button
            key={value}
            className={`rounded-xl px-4 py-2 text-sm transition ${
              filter === value ? "bg-neutral-100 text-neutral-950" : "border border-neutral-800 bg-neutral-900 text-neutral-300 hover:bg-neutral-800"
            }`}
            onClick={() => setFilter(value)}
          >
            {filterLabel(value, t)}
          </button>
        ))}
      </div>

      {panelError ? <div className="rounded-2xl border border-red-900/70 bg-red-950/40 p-4 text-sm text-red-200">{panelError}</div> : null}
      {notice ? <div className="rounded-2xl border border-emerald-900/70 bg-emerald-950/30 p-4 text-sm text-emerald-200">{notice}</div> : null}

      <McpPermissionsSection
        api={api}
        onChanged={setNotice}
        onError={(message) => {
          setPanelError(message);
          onError(message);
        }}
      />

      {loading ? <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4 text-sm text-neutral-400">{t("common.loading")}</div> : null}
      {!loading && visibleItems.length === 0 ? (
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-8 text-center text-sm text-neutral-400">{t("common.empty")}</div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        {visibleItems.map((item) => {
          const installedItem = installedByServer.get(item.server_name);
          const isInstalled = item.installed || Boolean(installedItem);
          const enabled = installedItem?.enabled ?? item.installed_enabled ?? false;
          const status = item.status ?? "available";
          return (
            <article key={`${item.id ?? item.server_name}-${item.server_name}`} className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate text-base font-semibold text-neutral-100">{item.display_name || item.server_name}</h3>
                  <p className="mt-1 font-mono text-xs text-neutral-500">{item.server_name}</p>
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-2">
                  {item.official ? <Badge icon={<ShieldCheck size={13} />} label={t("common.official")} /> : null}
                  <Badge label={statusLabel(status, t)} />
                  {isInstalled ? <Badge icon={<CheckCircle2 size={13} />} label={t("common.installed")} /> : null}
                </div>
              </div>

              {item.description ? <p className="mt-3 text-sm leading-6 text-neutral-300">{item.description}</p> : null}

              <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                <Detail label={t("common.category")} value={item.category} />
                <Detail label={t("common.version")} value={item.version} />
                <Detail label={t("common.status")} value={isInstalled ? (enabled ? t("common.enabled") : t("common.disabled")) : t("common.notInstalled")} />
                <Detail label="transport" value={item.transport} />
                <Detail label="url" value={item.url_preview} />
                <Detail label={t("common.environment")} value={mergeKeys(item.env_keys, item.sensitive_env_keys).join(", ")} />
                <Detail label="headers" value={mergeKeys(item.header_keys, item.sensitive_header_keys).join(", ")} />
              </div>

              <ListSection label={t("common.tags")} values={item.tags} />
              <ListSection label="runtime.requires" values={item.runtime?.requires} />
              <ListSection label={`${t("common.permissions")} default`} values={item.permissions?.default} />
              <ListSection label={`${t("common.permissions")} available`} values={item.permissions?.available} />
              {item.resource_scope?.description ? <p className="mt-3 text-xs leading-5 text-neutral-500">{item.resource_scope.description}</p> : null}

              <div className="mt-4 flex flex-wrap gap-2">
                {renderActions({
                  item,
                  isInstalled,
                  enabled,
                  workingKey,
                  t,
                  onInstall: () => install(item),
                  onEnable: () => enable(item.server_name),
                  onDisable: () => disable(item.server_name),
                  onUninstall: () => uninstall(item.server_name),
                })}
              </div>

              {status === "coming_soon" ? <p className="mt-3 text-xs text-neutral-500">{t("text.mcpComingSoon")}</p> : null}
            </article>
          );
        })}
      </div>

      {catalog?.errors?.length || catalog?.diagnostics ? (
        <details className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
          <summary className="cursor-pointer text-sm text-neutral-300">Diagnostics</summary>
          <div className="mt-3">
            <JsonBlock value={redactSecrets({ errors: catalog.errors, diagnostics: catalog.diagnostics })} />
          </div>
        </details>
      ) : null}
    </PanelShell>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-950 p-3">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-neutral-100">{value}</p>
    </div>
  );
}

function Badge({ label, icon }: { label: string; icon?: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs text-neutral-300">
      {icon}
      {label}
    </span>
  );
}

function Detail({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="mt-1 break-words text-neutral-200">{value || "-"}</p>
    </div>
  );
}

function ListSection({ label, values }: { label: string; values?: string[] }) {
  if (!values?.length) {
    return null;
  }
  return (
    <div className="mt-3">
      <p className="text-xs text-neutral-500">{label}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {values.map((value) => (
          <span key={value} className="rounded-full bg-neutral-950 px-2 py-1 text-xs text-neutral-300">
            {value}
          </span>
        ))}
      </div>
    </div>
  );
}

function DependencyCard({ name, status }: { name: string; status?: MCPDependencyStatus }) {
  const found = Boolean(status?.found);
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-950 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium text-neutral-100">{name}</p>
        <span className={found ? "text-emerald-300" : "text-amber-300"}>{found ? "available" : "missing"}</span>
      </div>
      <p className="mt-2 break-words font-mono text-xs text-neutral-400">{status?.resolved_path || status?.command || "-"}</p>
      {status?.version ? <p className="mt-2 text-xs text-neutral-500">{status.version}</p> : null}
      {status?.suggested_fix && !found ? <p className="mt-2 text-xs leading-5 text-amber-200">{status.suggested_fix}</p> : null}
    </div>
  );
}

function ServerSuggestedFixes({ diagnostics, title }: { diagnostics: unknown; title: string }) {
  if (!diagnostics || typeof diagnostics !== "object") {
    return null;
  }
  const entries = Object.entries(diagnostics as Record<string, unknown>).flatMap(([server, value]) => {
    if (!value || typeof value !== "object") {
      return [];
    }
    const record = value as Record<string, unknown>;
    const fix = typeof record.suggested_fix === "string" ? record.suggested_fix : "";
    if (!fix) {
      return [];
    }
    const code = typeof record.error_code === "string" ? record.error_code : "";
    return [{ server, fix, code }];
  });
  if (!entries.length) {
    return null;
  }
  return (
    <div className="mt-4 rounded-xl border border-amber-900/60 bg-amber-950/20 p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-amber-200">{title}</p>
      <div className="mt-2 space-y-2">
        {entries.map((entry) => (
          <p key={entry.server} className="text-xs leading-5 text-amber-100">
            <span className="font-mono text-amber-300">{entry.server}</span>
            {entry.code ? ` (${entry.code})` : ""}: {entry.fix}
          </p>
        ))}
      </div>
    </div>
  );
}

function renderActions({
  item,
  isInstalled,
  enabled,
  workingKey,
  t,
  onInstall,
  onEnable,
  onDisable,
  onUninstall,
}: {
  item: MCPMarketplaceItem;
  isInstalled: boolean;
  enabled: boolean;
  workingKey: string;
  t: ReturnType<typeof useI18n>["t"];
  onInstall: () => void;
  onEnable: () => void;
  onDisable: () => void;
  onUninstall: () => void;
}) {
  const status = item.status ?? "available";
  const busy = workingKey.endsWith(`:${item.server_name}`);
  if (status === "coming_soon") {
    return <DisabledAction icon={<CircleOff size={16} />} label={t("common.comingSoon")} />;
  }
  if (status === "disabled") {
    return <DisabledAction icon={<CircleOff size={16} />} label={t("common.disabled")} />;
  }
  if (!isInstalled) {
    return (
      <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={busy || item.installable === false} onClick={onInstall}>
        <Download size={16} />
        {busy ? t("common.loading") : t("actions.install")}
      </button>
    );
  }
  return (
    <>
      {enabled ? (
        <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={busy} onClick={onDisable}>
          <PowerOff size={16} />
          {busy ? t("common.loading") : t("actions.disable")}
        </button>
      ) : (
        <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={busy} onClick={onEnable}>
          <Power size={16} />
          {busy ? t("common.loading") : t("actions.enable")}
        </button>
      )}
      <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={busy} onClick={onUninstall}>
        <Trash2 size={16} />
        {busy ? t("common.loading") : t("actions.uninstall")}
      </button>
    </>
  );
}

function DisabledAction({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <button className={`${buttonClass} inline-flex items-center gap-2`} disabled>
      {icon}
      {label}
    </button>
  );
}

function filterLabel(filter: Filter, t: ReturnType<typeof useI18n>["t"]): string {
  if (filter === "available") {
    return t("common.available");
  }
  if (filter === "installed") {
    return t("common.installed");
  }
  if (filter === "coming_soon") {
    return t("common.comingSoon");
  }
  return "All";
}

function statusLabel(status: string, t: ReturnType<typeof useI18n>["t"]): string {
  if (status === "available") {
    return t("common.available");
  }
  if (status === "coming_soon") {
    return t("common.comingSoon");
  }
  if (status === "disabled") {
    return t("common.disabled");
  }
  return status;
}

function mergeKeys(envKeys?: string[], sensitiveEnvKeys?: string[]) {
  return Array.from(new Set([...(envKeys ?? []), ...(sensitiveEnvKeys ?? [])]));
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function stringValue(value: unknown): string {
  return typeof value === "string" && value ? value : "-";
}

function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redactSecrets);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, entry]) => {
      if (/token|secret|password|api[_-]?key|env[_-]?values/i.test(key)) {
        return [key, "[redacted]"];
      }
      return [key, redactSecrets(entry)];
    }),
  );
}
