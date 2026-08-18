import { RefreshCw, Search } from "lucide-react";
import { useState } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import { buttonClass, Field, inputClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  api: ReturnType<typeof createApiClient>;
  status: unknown;
  setStatus: (value: unknown) => void;
  onError: (message: string) => void;
}

export default function RagPanel({ api, status, setStatus, onError }: Props) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"hybrid" | "semantic" | "keyword">("hybrid");
  const [topK, setTopK] = useState(5);
  const [result, setResult] = useState<unknown>(null);

  async function refreshStatus() {
    try {
      setStatus(await api.ragStatus());
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.ragStatus"));
    }
  }

  async function runQuery() {
    try {
      setResult(await api.ragQuery({ query, mode, top_k: topK }));
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.ragQuery"));
    }
  }

  return (
    <PanelShell title={t("panels.rag")}>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <Field label={t("fields.query")}>
          <textarea
            className={`${inputClass} min-h-24 resize-y`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask with retrieval..."
          />
        </Field>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <Field label={t("fields.mode")}>
            <select className={inputClass} value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="hybrid">hybrid</option>
              <option value="semantic">semantic</option>
              <option value="keyword">keyword</option>
            </select>
          </Field>
          <Field label="top_k">
            <input className={inputClass} type="number" min={1} max={20} value={topK} onChange={(e) => setTopK(Number(e.target.value))} />
          </Field>
        </div>
        <div className="mt-4 flex gap-2">
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={!query.trim()} onClick={runQuery}>
            <Search size={16} />
            {t("actions.query")}
          </button>
          <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refreshStatus}>
            <RefreshCw size={16} />
            {t("actions.status")}
          </button>
        </div>
      </div>
      {status ? <JsonBlock value={status} /> : null}
      {result ? <JsonBlock value={result} /> : null}
    </PanelShell>
  );
}
