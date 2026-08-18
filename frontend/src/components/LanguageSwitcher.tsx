import { Globe2 } from "lucide-react";
import { useI18n, type Language } from "../i18n";

const languages: Language[] = ["en", "zh", "ja"];
const languageKeys: Record<Language, "language.en" | "language.zh" | "language.ja"> = {
  en: "language.en",
  zh: "language.zh",
  ja: "language.ja",
};

export default function LanguageSwitcher() {
  const { language, setLanguage, t, languageNames } = useI18n();
  const activeIndex = languages.indexOf(language);

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-950 p-2" aria-label={t("language.label")}>
      <div className="mb-2 flex items-center gap-2 px-1 text-xs text-neutral-500">
        <Globe2 size={14} />
        <span>{t("language.label")}</span>
      </div>
      <div className="relative grid h-9 grid-cols-3 rounded-lg bg-neutral-900 p-1">
        <span
          className="absolute bottom-1 left-1 top-1 rounded-md bg-neutral-100 transition-transform duration-300 ease-out"
          style={{ width: "calc((100% - 0.5rem) / 3)", transform: `translateX(${activeIndex * 100}%)` }}
        />
        {languages.map((item) => (
          <button
            key={item}
            type="button"
            className={`relative z-10 rounded-md text-xs font-medium transition-colors duration-200 ${
              language === item ? "text-neutral-950" : "text-neutral-400 hover:text-neutral-100"
            }`}
            onClick={() => setLanguage(item)}
            title={languageNames[item]}
            aria-pressed={language === item}
          >
            {t(languageKeys[item])}
          </button>
        ))}
      </div>
    </div>
  );
}
