import { FilePlus, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import { buttonClass, Field, inputClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  onError: (message: string) => void;
}

export default function DocumentPanel({ api, onError }: Props) {
  const { t } = useI18n();
  const [path, setPath] = useState("");
  const [loadResult, setLoadResult] = useState<unknown>(null);
  const [documents, setDocuments] = useState<unknown>(null);

  async function load() {
    try {
      setLoadResult(await api.loadDocument({ path, create_chunks: true }));
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.documentLoad"));
    }
  }

  async function list() {
    try {
      setDocuments(await api.listDocuments());
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.documentList"));
    }
  }

  return (
    <PanelShell title={t("panels.documents")}>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <p className="mb-4 text-sm text-neutral-400">{t("text.documentsHelp")}</p>
        <Field label={t("fields.documentPath")}>
          <input className={inputClass} value={path} onChange={(e) => setPath(e.target.value)} placeholder="README.md" />
        </Field>
        <div className="mt-4 flex gap-2">
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={!path.trim()} onClick={load}>
            <FilePlus size={16} />
            {t("actions.load")}
          </button>
          <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={list}>
            <RefreshCw size={16} />
            {t("actions.list")}
          </button>
        </div>
      </div>
      {loadResult ? <JsonBlock value={loadResult} /> : null}
      {documents ? <JsonBlock value={documents} /> : null}
    </PanelShell>
  );
}
