import { RefreshCw } from "lucide-react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import type { WorkspaceStatusResponse } from "../types/api";
import { buttonClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  status: WorkspaceStatusResponse | null;
  setStatus: (value: WorkspaceStatusResponse | null) => void;
  onError: (message: string) => void;
}

export default function WorkspacePanel({ api, status, setStatus, onError }: Props) {
  const { t } = useI18n();

  async function refresh() {
    try {
      setStatus(await api.getWorkspaceStatus());
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.workspaceStatus"));
    }
  }

  return (
    <PanelShell title={t("panels.workspace")}>
      <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refresh}>
        <RefreshCw size={16} />
        {t("actions.refresh")}
      </button>
      {status ? (
        <div className="grid gap-3 md:grid-cols-2">
          {[
            ["user_id", status.user_id],
            ["project_id", status.project_id],
            ["documents_count", status.documents_count],
            ["chunks_count", status.chunks_count],
            ["vectors_count", status.vectors_count],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
              <p className="text-xs text-neutral-500">{label}</p>
              <p className="mt-2 text-lg text-neutral-100">{String(value)}</p>
            </div>
          ))}
        </div>
      ) : null}
      {status ? <JsonBlock value={{ memory_counts: status.memory_counts, workspace_id: status.workspace_id }} /> : null}
    </PanelShell>
  );
}
