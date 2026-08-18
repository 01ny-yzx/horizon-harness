import type { ReactNode } from "react";
import { useI18n } from "../i18n";

export function PanelShell({ title, children }: { title: string; children: ReactNode }) {
  const { language } = useI18n();

  return (
    <main className="min-w-0 flex-1 overflow-y-auto bg-neutral-950 p-6">
      <div className="mx-auto max-w-4xl">
        <h2 key={`title-${language}-${title}`} className="animate-language-fade text-xl font-semibold text-neutral-100">
          {title}
        </h2>
        <div key={`body-${language}`} className="animate-language-fade mt-5 space-y-4">
          {children}
        </div>
      </div>
    </main>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-xl border border-neutral-800 bg-black p-3 text-xs leading-5 text-neutral-300">
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full rounded-xl border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none transition placeholder:text-neutral-500 focus:border-neutral-400";

export const buttonClass =
  "rounded-xl bg-neutral-100 px-4 py-2 text-sm font-medium text-neutral-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-500";
