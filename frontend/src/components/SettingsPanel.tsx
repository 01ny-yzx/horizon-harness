import { Eye, EyeOff, Plug, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { createApiClient } from "../api/client";
import { useI18n } from "../i18n";
import { buttonClass, Field, inputClass, JsonBlock, PanelShell } from "./PanelHelpers";

interface Props {
  apiBaseUrl: string;
  apiKey: string;
  userId: string;
  projectId: string;
  setApiBaseUrl: (value: string) => void;
  setApiKey: (value: string) => void;
  setUserId: (value: string) => void;
  setProjectId: (value: string) => void;
  api: ReturnType<typeof createApiClient>;
  onError: (message: string) => void;
}

interface BackendStatus {
  success?: boolean;
  data?: {
    llm?: {
      provider?: string;
      model?: string;
      base_url?: string;
      api_key_configured?: boolean;
      temperature?: number;
      timeout?: number;
      reasoning_mode?: string;
      extra_body_configured?: boolean;
      reasoning?: {
        mode?: string;
        auto_policy?: string;
        extra_body_configured?: boolean;
      };
      capability_preset?: string;
      capability_preset_requested?: string;
      capability_preset_effective?: string;
      capability_preset_source?: string;
      capabilities?: {
        supports_tools?: boolean;
        supports_reasoning?: boolean;
        requires_reasoning_echo?: boolean;
        supports_disable_reasoning?: boolean;
        supports_extra_body?: boolean;
        supports_json_mode?: boolean | null;
        supports_streaming?: boolean | null;
        max_context_tokens?: number | null;
      };
    };
  };
  error?: string;
}

export default function SettingsPanel(props: Props) {
  const { t } = useI18n();
  const [showKey, setShowKey] = useState(false);
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [testing, setTesting] = useState(false);

  async function refreshStatus() {
    setTesting(true);
    try {
      setStatus((await props.api.status()) as BackendStatus);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("errors.connectionTest");
      setStatus({ success: false, error: message });
      props.onError(message);
    } finally {
      setTesting(false);
    }
  }

  const llm = status?.data?.llm;
  const capabilities = llm?.capabilities;
  const unknown = t("common.unknown");
  const boolValue = (value: boolean | null | undefined) => (value == null ? unknown : value ? t("values.yes") : t("values.no"));
  const presetSource = (value?: string) => {
    if (value === "model") return t("values.autoDetectedFromModel");
    if (value === "explicit") return t("values.manualOverride");
    if (value === "provider") return t("values.providerDefault");
    if (value === "override") return t("values.runtimeOverride");
    if (value === "default") return t("values.safeDefault");
    return value || unknown;
  };

  return (
    <PanelShell title={t("panels.settings")}>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label={t("fields.apiBaseUrl")}>
            <input className={inputClass} value={props.apiBaseUrl} onChange={(e) => props.setApiBaseUrl(e.target.value)} />
          </Field>
          <Field label={t("fields.apiKey")}>
            <div className="flex gap-2">
              <input
                className={inputClass}
                type={showKey ? "text" : "password"}
                value={props.apiKey}
                placeholder={t("settings.apiKeyPlaceholder")}
                onChange={(e) => props.setApiKey(e.target.value)}
              />
              <button
                className="flex h-10 w-10 items-center justify-center rounded-xl border border-neutral-700 text-neutral-300 hover:bg-neutral-800"
                onClick={() => setShowKey((value) => !value)}
                title={showKey ? t("settings.hideApiKey") : t("settings.showApiKey")}
              >
                {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </Field>
          <Field label="user_id">
            <input className={inputClass} value={props.userId} onChange={(e) => props.setUserId(e.target.value)} />
          </Field>
          <Field label="project_id">
            <input className={inputClass} value={props.projectId} onChange={(e) => props.setProjectId(e.target.value)} />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refreshStatus} disabled={testing}>
            <Plug size={16} />
            {testing ? t("chat.thinking") : t("actions.testConnection")}
          </button>
          <button className={`${buttonClass} inline-flex items-center gap-2`} onClick={refreshStatus} disabled={testing}>
            <RefreshCw size={16} />
            {t("actions.refreshStatus")}
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-4">
        <div className="mb-3 text-sm font-semibold text-neutral-100">{t("panels.backendLlmConfig")}</div>
        <div className="grid gap-3 md:grid-cols-2">
          <StatusField label={t("fields.llmProvider")} value={llm?.provider ?? unknown} />
          <StatusField label={t("fields.llmModel")} value={llm?.model ?? unknown} />
          <StatusField label={t("fields.llmBaseUrl")} value={llm?.base_url ?? unknown} />
          <StatusField label={t("fields.llmApiKeyConfigured")} value={llm?.api_key_configured ? t("values.yes") : llm ? t("values.no") : unknown} />
          <StatusField label={t("fields.temperature")} value={llm?.temperature ?? unknown} />
          <StatusField label={t("fields.timeout")} value={llm?.timeout ?? unknown} />
          <StatusField label={t("fields.reasoningMode")} value={llm?.reasoning?.mode ?? llm?.reasoning_mode ?? unknown} />
          <StatusField label={t("fields.reasoningAutoPolicy")} value={llm?.reasoning?.auto_policy ?? unknown} />
          <StatusField
            label={t("fields.extraBodyConfigured")}
            value={(llm?.reasoning?.extra_body_configured ?? llm?.extra_body_configured) ? t("values.yes") : llm ? t("values.no") : unknown}
          />
          <StatusField label={t("fields.capabilityPresetRequested")} value={llm?.capability_preset_requested || t("values.auto")} />
          <StatusField label={t("fields.capabilityPresetEffective")} value={llm?.capability_preset_effective || llm?.capability_preset || unknown} />
          <StatusField label={t("fields.capabilityPresetSource")} value={presetSource(llm?.capability_preset_source)} />
          <StatusField label={t("fields.supportsTools")} value={boolValue(capabilities?.supports_tools)} />
          <StatusField label={t("fields.supportsReasoning")} value={boolValue(capabilities?.supports_reasoning)} />
          <StatusField label={t("fields.requiresReasoningEcho")} value={boolValue(capabilities?.requires_reasoning_echo)} />
          <StatusField label={t("fields.supportsDisableReasoning")} value={boolValue(capabilities?.supports_disable_reasoning)} />
          <StatusField label={t("fields.supportsExtraBody")} value={boolValue(capabilities?.supports_extra_body)} />
          <StatusField label={t("fields.supportsJsonMode")} value={boolValue(capabilities?.supports_json_mode)} />
          <StatusField label={t("fields.supportsStreaming")} value={boolValue(capabilities?.supports_streaming)} />
          <StatusField label={t("fields.maxContextTokens")} value={capabilities?.max_context_tokens ?? unknown} />
        </div>
      </div>

      {status ? <JsonBlock value={status} /> : null}
    </PanelShell>
  );
}

function StatusField(props: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2">
      <div className="text-xs text-neutral-500">{props.label}</div>
      <div className="mt-1 break-words text-sm text-neutral-100">{String(props.value)}</div>
    </div>
  );
}
