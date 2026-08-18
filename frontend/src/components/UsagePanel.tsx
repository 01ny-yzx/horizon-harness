import { RefreshCw } from "lucide-react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import { buttonClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  usageStatus: unknown;
  cacheStatus: unknown;
  setUsageStatus: (value: unknown) => void;
  setCacheStatus: (value: unknown) => void;
  onError: (message: string) => void;
}

export default function UsagePanel({ api, usageStatus, cacheStatus, setUsageStatus, setCacheStatus, onError }: Props) {
  const { t } = useI18n();

  async function refreshUsage() {
    try {
      setUsageStatus(await api.usageStatus());
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.usageStatus"));
    }
  }

  async function refreshCache() {
    try {
      setCacheStatus(await api.cacheStatus());
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.cacheStatus"));
    }
  }

  return (
    <PanelShell title={t("panels.usageCache")}>
      <div className="flex flex-wrap gap-2">
        <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refreshUsage}>
          <RefreshCw size={16} />
          {t("actions.usageStatus")}
        </button>
        <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refreshCache}>
          <RefreshCw size={16} />
          {t("actions.cacheStatus")}
        </button>
      </div>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <p className="mb-3 text-sm text-neutral-400">{t("text.todayUsage")}</p>
        {usageStatus ? <JsonBlock value={usageStatus} /> : null}
      </div>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <p className="mb-3 text-sm text-neutral-400">{t("status.cache")}</p>
        {cacheStatus ? <JsonBlock value={cacheStatus} /> : null}
      </div>
    </PanelShell>
  );
}
