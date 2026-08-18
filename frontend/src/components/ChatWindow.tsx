import type { ChatMessage as ChatMessageType } from "../types/api";
import { useI18n } from "../i18n";
import ChatInput from "./ChatInput";
import ChatMessage from "./ChatMessage";

interface Props {
  messages: ChatMessageType[];
  loading: boolean;
  userId: string;
  projectId: string;
  apiBaseUrl: string;
  onSend: (message: string) => void;
}

export default function ChatWindow({ messages, loading, userId, projectId, apiBaseUrl, onSend }: Props) {
  const { t, language } = useI18n();

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-neutral-950">
      <header className="flex items-center justify-between border-b border-neutral-800 px-6 py-4">
        <div className="transition-opacity duration-200" key={`chat-header-${language}`}>
          <p className="text-sm font-medium text-neutral-100">{t("nav.chat")}</p>
          <p className="mt-1 text-xs text-neutral-500">
            {userId} / {projectId}
          </p>
        </div>
        {loading ? <span className="text-xs text-neutral-400">{t("chat.thinking")}</span> : null}
      </header>
      <section className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-4xl flex-col gap-4">
          {messages.length === 0 ? (
            <div className="rounded-2xl border border-neutral-800 bg-neutral-900 p-6 text-sm text-neutral-300 transition-opacity duration-200" key={language}>
              {t("chat.empty")}
            </div>
          ) : (
            messages.map((message) => <ChatMessage key={message.id} message={message} apiBaseUrl={apiBaseUrl} />)
          )}
        </div>
      </section>
      <ChatInput loading={loading} onSend={onSend} />
    </main>
  );
}
