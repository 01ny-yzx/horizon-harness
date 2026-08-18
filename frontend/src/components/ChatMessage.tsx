import ReactMarkdown from "react-markdown";
import type { ChatMessage as ChatMessageType } from "../types/api";

interface Props {
  message: ChatMessageType;
  apiBaseUrl?: string;
}

export default function ChatMessage({ message, apiBaseUrl = "" }: Props) {
  const isUser = message.role === "user";
  const isError = message.role === "error";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={[
          "max-w-[82%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm",
          isUser ? "bg-neutral-100 text-neutral-950" : "border border-neutral-800 bg-neutral-900 text-neutral-100",
          isError ? "border-red-500 text-red-300" : "",
        ].join(" ")}
      >
        {message.role === "assistant" ? (
          <ReactMarkdown
            components={{
              code({ children }) {
                return <code className="rounded bg-neutral-950 px-1.5 py-0.5 text-neutral-100">{children}</code>;
              },
              pre({ children }) {
                return <pre className="my-3 overflow-x-auto rounded-xl bg-black p-3 text-xs">{children}</pre>;
              },
              a({ children, href }) {
                const resolvedHref =
                  href && href.startsWith("/files/") && apiBaseUrl ? `${apiBaseUrl.replace(/\/$/, "")}${href}` : href;
                return (
                  <a className="text-white underline underline-offset-4" href={resolvedHref} target="_blank" rel="noreferrer">
                    {children}
                  </a>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        ) : (
          message.content
        )}
      </div>
    </div>
  );
}
