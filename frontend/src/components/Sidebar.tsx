import { Bot, Database, FileText, Gauge, Globe2, MessageSquare, Plus, Settings, Sparkles, Store, UserCog } from "lucide-react";
import { useI18n } from "../i18n";
import LanguageSwitcher from "./LanguageSwitcher";

export type PanelName = "chat" | "workspace" | "documents" | "memory" | "rag" | "browser" | "mcp" | "usage" | "settings";

interface Props {
  selectedPanel: PanelName;
  userId: string;
  projectId: string;
  onSelect: (panel: PanelName) => void;
  onNewChat: () => void;
}

const items: Array<{ id: PanelName; navKey: Parameters<ReturnType<typeof useI18n>["t"]>[0]; icon: typeof MessageSquare }> = [
  { id: "chat", navKey: "nav.chat", icon: MessageSquare },
  { id: "workspace", navKey: "nav.workspace", icon: Database },
  { id: "documents", navKey: "nav.documents", icon: FileText },
  { id: "memory", navKey: "nav.memory", icon: UserCog },
  { id: "rag", navKey: "nav.rag", icon: Sparkles },
  { id: "browser", navKey: "nav.browser", icon: Globe2 },
  { id: "mcp", navKey: "nav.mcpMarketplace", icon: Store },
  { id: "usage", navKey: "nav.usage", icon: Gauge },
  { id: "settings", navKey: "nav.settings", icon: Settings },
];

export default function Sidebar({ selectedPanel, userId, projectId, onSelect, onNewChat }: Props) {
  const { t } = useI18n();

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-neutral-800 bg-neutral-900 p-4">
      <div className="mb-5 flex items-center gap-3 px-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-neutral-100 text-neutral-950">
          <Bot size={20} />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-neutral-100">Agent</h1>
          <p className="text-xs text-neutral-500">{t("app.subtitle")}</p>
        </div>
      </div>
      <div className="mb-4">
        <LanguageSwitcher />
      </div>
      <button
        className="mb-4 flex items-center justify-center gap-2 rounded-xl bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-950 transition hover:bg-white"
        onClick={onNewChat}
      >
        <Plus size={16} />
        {t("actions.newChat")}
      </button>
      <div className="mb-4 rounded-xl border border-neutral-800 bg-neutral-950 p-3 text-xs text-neutral-400">
        <p className="truncate text-neutral-100">{userId}</p>
        <p className="mt-1 truncate">{projectId}</p>
      </div>
      <nav className="space-y-1">
        {items.map((item) => {
          const Icon = item.icon;
          const active = selectedPanel === item.id;
          return (
            <button
              key={item.id}
              className={`flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition ${
                active ? "bg-neutral-100 text-neutral-950" : "text-neutral-300 hover:bg-neutral-800 hover:text-white"
              }`}
              onClick={() => onSelect(item.id)}
            >
              <Icon size={16} />
              {t(item.navKey)}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
