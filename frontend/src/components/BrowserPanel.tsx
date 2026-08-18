import { Camera, FileText, Link2, RefreshCw } from "lucide-react";
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

export default function BrowserPanel({ api, status, setStatus, onError }: Props) {
  const { t } = useI18n();
  const [url, setUrl] = useState("https://example.com");
  const [result, setResult] = useState<unknown>(null);
  const browserData = readData(status);
  const browserChannel = typeof browserData?.browser_channel === "string" ? browserData.browser_channel : "";

  async function refreshStatus() {
    try {
      setStatus(await api.browserStatus());
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.browserStatus"));
    }
  }

  async function run(action: "text" | "links" | "screenshot") {
    try {
      if (action === "text") setResult(await api.browserExtractText({ url }));
      if (action === "links") setResult(await api.browserListLinks({ url }));
      if (action === "screenshot") setResult(await api.browserScreenshot({ url, full_page: false }));
    } catch (error) {
      onError(error instanceof Error ? error.message : t("errors.browserRequest"));
    }
  }

  return (
    <PanelShell title={t("panels.browser")}>
      <div className="flex flex-wrap gap-2">
        <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refreshStatus}>
          <RefreshCw size={16} />
          {t("actions.status")}
        </button>
      </div>
      {status ? (
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4 text-sm text-neutral-300">
          <p>{browserChannel ? t("text.browserSystem", { channel: browserChannel }) : t("text.browserDefault")}</p>
          <p className="mt-2 text-xs text-neutral-500">{t("text.browserPurpose")}</p>
        </div>
      ) : null}

      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <Field label={t("fields.url")}>
          <input className={inputClass} value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com" />
        </Field>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={!url.trim()} onClick={() => run("text")}>
            <FileText size={16} />
            {t("actions.extractText")}
          </button>
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={!url.trim()} onClick={() => run("links")}>
            <Link2 size={16} />
            {t("actions.listLinks")}
          </button>
          <button className={`${buttonClass} inline-flex items-center gap-2`} disabled={!url.trim()} onClick={() => run("screenshot")}>
            <Camera size={16} />
            {t("actions.screenshot")}
          </button>
        </div>
      </div>

      {status ? <JsonBlock value={status} /> : null}
      {result ? <JsonBlock value={result} /> : null}
    </PanelShell>
  );
}

function readData(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || !("data" in value)) return null;
  const data = (value as { data?: unknown }).data;
  return data && typeof data === "object" ? (data as Record<string, unknown>) : null;
}
