import { Activity, AlertTriangle, Database, Gauge } from "lucide-react";
import { useI18n } from "../i18n";
import type { WorkspaceStatusResponse } from "../types/api";

interface Props {
  workspaceStatus: WorkspaceStatusResponse | null;
  ragStatus: unknown;
  usageStatus: unknown;
  lastError: string;
}

export default function StatusPanel({ workspaceStatus, ragStatus, usageStatus, lastError }: Props) {
  const { t } = useI18n();

  return (
    <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-neutral-800 bg-neutral-950 p-4 xl:block">
      <h2 className="text-sm font-semibold text-neutral-100">{t("status.title")}</h2>
      <InfoCard icon={<Database size={16} />} label={t("status.workspace")}>
        {workspaceStatus ? (
          <>
            <p>
              {t("status.documents")}: {workspaceStatus.documents_count}
            </p>
            <p>
              {t("status.chunks")}: {workspaceStatus.chunks_count}
            </p>
            <p>
              {t("status.vectors")}: {workspaceStatus.vectors_count}
            </p>
          </>
        ) : (
          <p>{t("status.noWorkspace")}</p>
        )}
      </InfoCard>
      <InfoCard icon={<Activity size={16} />} label={t("nav.rag")}>
        <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(ragStatus ?? {}, null, 2).slice(0, 500)}</pre>
      </InfoCard>
      <InfoCard icon={<Gauge size={16} />} label={t("status.usage")}>
        <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(usageStatus ?? {}, null, 2).slice(0, 500)}</pre>
      </InfoCard>
      <InfoCard icon={<AlertTriangle size={16} />} label={t("status.recentError")}>
        <p className={lastError ? "text-red-300" : ""}>{lastError || t("status.noRecentErrors")}</p>
      </InfoCard>
    </aside>
  );
}

function InfoCard({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-xl border border-neutral-800 bg-neutral-900 p-3 text-xs leading-5 text-neutral-400">
      <div className="mb-2 flex items-center gap-2 text-neutral-100">
        {icon}
        <span>{label}</span>
      </div>
      {children}
    </div>
  );
}
