import { RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import { buttonClass, Field, inputClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  onError: (message: string) => void;
}

type DeleteMode = "reference" | "preference" | "project_instruction" | "clear_type";

export default function MemoryPanel({ api, onError }: Props) {
  const { t } = useI18n();
  const [memories, setMemories] = useState<unknown>(null);
  const [deleteMode, setDeleteMode] = useState<DeleteMode>("reference");
  const [deleteTarget, setDeleteTarget] = useState("");
  const [deleteResult, setDeleteResult] = useState<unknown>(null);

  async function refresh() {
    try {
      setMemories(await api.listMemory({ memory_type: "all" }));
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.memoryRead"));
    }
  }

  async function deleteExact() {
    try {
      const target = deleteTarget.trim();
      const result = deleteMode === "reference"
        ? await api.deleteMemoryReference({ reference_id: target })
        : deleteMode === "preference"
          ? await api.deleteUserPreference({ key: target })
          : deleteMode === "project_instruction"
            ? await api.deleteProjectInstruction({ content: target })
            : await api.clearMemoryType({ memory_type: target });
      setDeleteResult(result);
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.memoryDelete"));
    }
  }

  return (
    <PanelShell title={t("panels.memory")}>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <div className="flex flex-wrap gap-2">
          <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refresh}>
            <RefreshCw size={16} />
            {t("actions.listMemory")}
          </button>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-[12rem_1fr_auto]">
          <Field label={t("fields.memoryDeleteMode")}>
            <select className={inputClass} value={deleteMode} onChange={(e) => setDeleteMode(e.target.value as DeleteMode)}>
              <option value="reference">reference_id</option>
              <option value="preference">preference key</option>
              <option value="project_instruction">project instruction</option>
              <option value="clear_type">memory type</option>
            </select>
          </Field>
          <Field label={t("fields.memoryDeleteTarget")}>
            <input className={inputClass} value={deleteTarget} onChange={(e) => setDeleteTarget(e.target.value)} />
          </Field>
          <button className={`${buttonClass} mt-6 inline-flex items-center gap-2`} disabled={!deleteTarget.trim()} onClick={deleteExact}>
            <Trash2 size={16} />
            {t("actions.deleteMemory")}
          </button>
        </div>
      </div>
      {deleteResult ? <JsonBlock value={deleteResult} /> : null}
      {memories ? <JsonBlock value={memories} /> : null}
    </PanelShell>
  );
}
