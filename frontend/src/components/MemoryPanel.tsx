import { RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import { buttonClass, Field, inputClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  onError: (message: string) => void;
}

export default function MemoryPanel({ api, onError }: Props) {
  const { t } = useI18n();
  const [memories, setMemories] = useState<unknown>(null);
  const [keyword, setKeyword] = useState("");
  const [forgetResult, setForgetResult] = useState<unknown>(null);

  async function refresh() {
    try {
      setMemories(await api.listMemory({ memory_type: "all" }));
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.memoryRead"));
    }
  }

  async function forget() {
    try {
      setForgetResult(await api.forgetMemory({ memory_type: "all", keyword }));
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.memoryForget"));
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
        <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto]">
          <Field label={t("fields.forgetKeyword")}>
            <input className={inputClass} value={keyword} onChange={(e) => setKeyword(e.target.value)} />
          </Field>
          <button className={`${buttonClass} mt-6 inline-flex items-center gap-2`} disabled={!keyword.trim()} onClick={forget}>
            <Trash2 size={16} />
            {t("actions.forget")}
          </button>
        </div>
      </div>
      {forgetResult ? <JsonBlock value={forgetResult} /> : null}
      {memories ? <JsonBlock value={memories} /> : null}
    </PanelShell>
  );
}
