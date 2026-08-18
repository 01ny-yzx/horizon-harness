import { Send } from "lucide-react";
import { useState } from "react";
import { useI18n } from "../i18n";

interface Props {
  loading: boolean;
  onSend: (message: string) => void;
}

export default function ChatInput({ loading, onSend }: Props) {
  const { t } = useI18n();
  const [value, setValue] = useState("");

  function submit() {
    const message = value.trim();
    if (!message || loading) {
      return;
    }
    setValue("");
    onSend(message);
  }

  return (
    <div className="border-t border-neutral-800 bg-neutral-950 p-4">
      <div className="mx-auto flex max-w-4xl items-end gap-3 rounded-2xl border border-neutral-700 bg-neutral-900 p-3">
        <textarea
          className="max-h-40 min-h-[48px] flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-6 text-neutral-100 outline-none placeholder:text-neutral-500"
          placeholder={t("chat.placeholder")}
          value={value}
          disabled={loading}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <button
          className="flex h-10 w-10 items-center justify-center rounded-full bg-neutral-100 text-neutral-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-500"
          onClick={submit}
          disabled={loading || !value.trim()}
          title={t("actions.send")}
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}
