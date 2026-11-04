import { revealContent } from "./revealContent";
import RevealText from "./RevealText";
import ReleaseHistory from "./ReleaseHistory";
import AnimatedTerminal from "./AnimatedTerminal";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  ArrowDown,
  Check,
  Copy,
  Github,
  Sun,
  Moon,
  Terminal,
  ChevronRight,
  Command,
  Layers,
  ShieldCheck,
} from "lucide-react";
export type Release = {
  tag_name: string;
  html_url: string;
  published_at: string;
  body: string;
  assets: { name: string; size: number; browser_download_url: string }[];
};
const repo = "https://github.com/01ny-yzx/horizon-harness";
function Logo() {
  return (
    <svg viewBox="0 0 48 36" fill="none" aria-hidden="true">
      <path
        d="M9 23a15 15 0 0 1 30 0M2 25h44M10 32h28"
        stroke="currentColor"
        strokeWidth="3"
      />
      <path
        d="M24 1v4M5 9l4 4M43 9l-4 4"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}
export default function App() {
  const [en, setEn] = useState(
      () => localStorage.getItem("horizon-language") === "en",
    ),
    [dark, setDark] = useState(() => {
      const saved = localStorage.getItem("horizon-theme");
      return saved
        ? saved === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
    }),
    [platform, setPlatform] = useState(
      navigator.userAgent.includes("Windows") ? "windows" : "macos",
    ),
    [method, setMethod] = useState("npm"),
    [copied, setCopied] = useState(false),
    [releases, setReleases] = useState<Release[]>([]),
    [query, setQuery] = useState(""),
    [notice, setNotice] = useState(""),
    [screenshot, setScreenshot] = useState("home"),
    [activeSection, setActiveSection] = useState("");
  const t = (zh: string, english: string) => (en ? english : zh);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("horizon-theme", dark ? "dark" : "light");
  }, [dark]);
  useEffect(() => {
    document.documentElement.lang = en ? "en" : "zh-CN";
    localStorage.setItem("horizon-language", en ? "en" : "zh-CN");
  }, [en]);
  useEffect(() => {
    let live = true;
    let remoteLoaded = false;
    fetch("./releases.json")
      .then((r) => r.json())
      .then((r) => {
        if (live && !remoteLoaded) setReleases(r);
      })
      .catch(() => setNotice("GitHub Releases"));
    fetch(
      "https://api.github.com/repos/01ny-yzx/horizon-harness/releases?per_page=100",
    )
      .then((r) => {
        if (!r.ok) throw Error();
        return r.json();
      })
      .then((r) => {
        if (live) {
          remoteLoaded = true;
          setReleases(
            r.filter(
              (x: { draft: boolean; prerelease: boolean }) =>
                !x.draft && !x.prerelease,
            ),
          );
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    const targets = document.querySelectorAll(
      ".features > div, .section-heading, .download-layout, .screenshot-frame, .release-history",
    );
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-revealed");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.08 },
    );
    targets.forEach((target, index) => {
      target.classList.add("scroll-reveal");
      (target as HTMLElement).style.setProperty(
        "--reveal-delay",
        `${Math.min(index % 3, 2) * 70}ms`,
      );
      observer.observe(target);
    });
    return () => observer.disconnect();
  }, [releases]);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const current = ["download", "changelog"].find((id) => {
        const box = document.getElementById(id)?.getBoundingClientRect();
        return box && box.top <= 160 && box.bottom > 160;
      });
      setActiveSection(current || "");
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    update();
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  const latest = releases[0],
    version = latest?.tag_name || "v1.0.3";
  const commands: Record<string, string> = {
    npm: "npm install -g horizon-harness",
    brew: "brew install 01ny-yzx/tap/horizon",
    curl: `curl -fsSL ${repo}/releases/latest/download/install.sh | sh`,
  };
  async function copy() {
    try {
      await navigator.clipboard.writeText(commands[method]);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setNotice(
        t("复制失败，请手动选择命令", "Select the command to copy manually"),
      );
    }
  }
  const files =
    latest?.assets.filter((a) =>
      platform === "windows"
        ? /^horizon-.*-windows-x64\.zip$/.test(a.name)
        : /^horizon-.*-macos-(arm64|x64)\.tar\.gz$/.test(a.name),
    ) || [];
  return revealContent(
    <>
      <a className="skip" href="#download">
        {t("跳到下载", "Skip to downloads")}
      </a>
      <header>
        <a href="#" className="brand">
          <Logo />
          <span>
            Horizon<span className="brand-sub"> harness</span>
          </span>
        </a>
        <nav aria-label={t("主导航", "Main navigation")}>
          <a
            href="#download"
            aria-current={activeSection === "download" ? "location" : undefined}
          >
            {t("下载安装", "Download")}
          </a>
          <a
            href="#changelog"
            aria-current={
              activeSection === "changelog" ? "location" : undefined
            }
          >
            {t("更新日志", "Changelog")}
          </a>
          <a href={`${repo}/blob/main/docs/usage${en ? "" : ".zh-CN"}.md`}>
            {t("文档", "Docs")}
            <ArrowUpRight size={13} />
          </a>
        </nav>
        <div className="nav-actions">
          <a href={repo} aria-label="GitHub">
            <Github size={19} />
          </a>
          <button onClick={() => setEn(!en)} aria-label="Change language">
            {en ? "中" : "EN"}
          </button>
          <button
            onClick={() => setDark(!dark)}
            aria-label={t("切换明暗主题", "Toggle theme")}
          >
            {dark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </header>
      <main>
        <section className="hero">
          <div className="orbital" aria-hidden="true">
            <div className="sun-disc" />
            <i />
            <i />
            <i />
          </div>
          <a className="release-pill" href="#changelog">
            <span className="live-dot" />
            {version} {t("现已发布", "is here")}
            <span className="pill-divider" />
            {t("看看新变化", "See what’s new")}
            <ChevronRight size={14} />
          </a>
          <div className="hero-copy">
            <p className="eyebrow">YOUR IDEAS. A NEW HORIZON.</p>
            <h1>
              <RevealText text="Make it" />
              <br />
              <RevealText
                text="happen."
                className="hero-title-accent"
                delay={220}
              />
              <span className="cursor">_</span>
            </h1>
            <p className="intro">
              {t(
                "你的想法，下一步就是行动。",
                "Your next idea. Put into action.",
              )}
              <br />
              {t(
                "让 AI 在终端里，和你一起完成工作。",
                "An AI agent, right at home in your terminal.",
              )}
            </p>
            <div className="hero-actions">
              <a className="primary" href="#download">
                {t("开始使用 Horizon", "Get Horizon")}
                <ArrowDown size={17} />
              </a>
              <a className="text-link" href={repo}>
                {t("探索源代码", "Explore the source")}
                <ArrowUpRight size={16} />
              </a>
            </div>
            <p className="platform-note">
              macOS · Windows <span> / </span>
              {t("开源 · 本地运行", "Open source · Runs locally")}
            </p>
          </div>
          <div className="terminal-preview">
            <div className="window-top">
              <div className="traffic">
                <b />
                <b />
                <b />
              </div>
              <span>horizon — ~/workspace</span>
              <Terminal size={14} />
            </div>
            <AnimatedTerminal en={en} version={version} />
          </div>
          <div className="hero-bottom">
            <span>A NEW HORIZON</span>
            <span>{t("向下探索", "SCROLL TO EXPLORE")} ↓</span>
          </div>
        </section>
        <section className="features">
          <div>
            <Terminal />
            <h3>
              <RevealText
                text={t("在熟悉的终端里", "At home in your terminal")}
              />
            </h3>
            <p>
              {t(
                "流式回答、工具进度和任务排队，保持工作节奏。",
                "Streaming responses, live tool activity and queued follow-ups.",
              )}
            </p>
          </div>
          <div>
            <Layers />
            <h3>
              <RevealText
                text={t("模型，由你选择", "Your model. Your choice.")}
              />
            </h3>
            <p>
              {t(
                "连接自己的 API Key，保存模型，随时切换。",
                "Connect your API key, save models, and switch when you need.",
              )}
            </p>
          </div>
          <div>
            <ShieldCheck />
            <h3>
              <RevealText text={t("控制权，始终在你", "You’re in control")} />
            </h3>
            <p>
              {t(
                "本地会话与配置，一键切换只读和完全访问模式。",
                "Local sessions and settings, with read-only and full-access modes.",
              )}
            </p>
          </div>
        </section>
        <section id="download" className="section download">
          <div className="section-label">GET STARTED</div>
          <div className="section-heading">
            <div>
              <h2>
                <RevealText
                  text={t(
                    "下一步，从这里开始。",
                    "Your next chapter starts here.",
                  )}
                />
              </h2>
              <p>
                {t(
                  "选一种你习惯的方式，打开终端，运行 Horizon。",
                  "Choose your installation method. Open a terminal. Make it happen.",
                )}
              </p>
            </div>
            <a
              className="small-link"
              href={latest?.html_url || `${repo}/releases/latest`}
            >
              {version}
              <ArrowUpRight size={14} />
            </a>
          </div>
          <div className="download-layout">
            <div className="install-panel">
              <h3>
                <Command size={18} />
                {t("一行命令，即刻开始", "One command. Ready to go.")}
              </h3>
              <div
                className="tabs"
                role="tablist"
                aria-label="Installation method"
              >
                {["npm", "brew", "curl"].map((m) => (
                  <button
                    key={m}
                    role="tab"
                    aria-selected={method === m}
                    onClick={() => {
                      setMethod(m);
                      setCopied(false);
                    }}
                  >
                    {m === "brew" ? "Homebrew" : m}
                  </button>
                ))}
              </div>
              <div className="command">
                <span className="command-prefix">$</span>
                <code>{commands[method]}</code>
                <button
                  onClick={copy}
                  aria-label={
                    copied
                      ? t("已复制", "Copied")
                      : t("复制安装命令", "Copy install command")
                  }
                  className={copied ? "is-copied" : undefined}
                >
                  {copied ? <Check size={17} /> : <Copy size={17} />}
                </button>
              </div>
              <p className="install-note" role="status">
                {copied
                  ? t("已复制到剪贴板", "Copied to clipboard")
                  : method === "npm"
                    ? t(
                        "macOS / Windows · 需要 Node.js 18+",
                        "macOS / Windows · Requires Node.js 18+",
                      )
                    : t(
                        "适用于 macOS · 无需安装 Python",
                        "For macOS · No Python installation needed",
                      )}
              </p>
              <div className="start-line">
                <span>↳</span>
                {t("安装完成后，运行", "After installing, run")}{" "}
                <code>horizon</code>
              </div>
            </div>
            <div className="native-panel">
              <div className="native-heading">
                <h3>
                  <RevealText
                    text={t("直接下载运行包", "Download a native bundle")}
                  />
                </h3>
                <span>{t("已内置 Python", "Python included")}</span>
              </div>
              <div
                className="tabs platform-tabs"
                role="tablist"
                aria-label="Operating system"
              >
                {["macos", "windows"].map((p) => (
                  <button
                    key={p}
                    role="tab"
                    aria-selected={platform === p}
                    onClick={() => setPlatform(p)}
                  >
                    {p === "macos" ? "macOS" : "Windows"}
                  </button>
                ))}
              </div>
              <div className="file-list">
                {files.map((file) => (
                  <a href={file.browser_download_url} key={file.name}>
                    <div>
                      <strong>
                        {file.name.includes("arm64")
                          ? "Apple Silicon"
                          : platform === "windows"
                            ? "Windows x64"
                            : "Intel Mac"}
                      </strong>
                      <small>
                        {file.name.includes("arm64")
                          ? t("搭载 M 系列芯片的 Mac", "Mac with Apple silicon")
                          : t("64 位处理器", "64-bit processor")}{" "}
                        · {(file.size / 1024 / 1024).toFixed(1)} MB
                      </small>
                    </div>
                    <ArrowDown size={18} />
                  </a>
                ))}
                {!files.length && (
                  <a href={`${repo}/releases/latest`}>
                    {t("在 GitHub 查看运行包", "View downloads on GitHub")}
                    <ArrowUpRight size={16} />
                  </a>
                )}
              </div>
              <p className="native-note">
                {t(
                  "下载后解压，保留完整文件夹，在终端运行 horizon。",
                  "Extract the entire folder, then run horizon in your terminal.",
                )}
              </p>
            </div>
          </div>
        </section>
        <section className="showcase section">
          <div className="section-label">IN YOUR TERMINAL</div>
          <div className="section-heading">
            <div>
              <h2>
                <RevealText
                  text={t("专注想法，保持心流。", "Less friction. More flow.")}
                />
              </h2>
              <p>
                {t(
                  "从第一次提问，到下一项任务。熟悉的终端，更顺手的搭档。",
                  "From your first question to your next task. A familiar place to build.",
                )}
              </p>
            </div>
          </div>
          <div
            className="screenshot-selector"
            role="group"
            aria-label={t("选择界面预览", "Choose a preview")}
          >
            {[
              ["home", t("首页", "Home")],
              ["conversation", t("对话", "Conversation")],
              ["welcome", t("欢迎页", "Welcome")],
            ].map(([id, label]) => (
              <button
                type="button"
                key={id}
                aria-pressed={screenshot === id}
                onClick={() => setScreenshot(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="screenshot-frame">
            <div className="window-top">
              <div className="traffic">
                <b />
                <b />
                <b />
              </div>
              <span>
                Horizon Harness ·{" "}
                {t("真实终端界面", "Actual terminal interface")}
              </span>
            </div>
            <div className="screenshot-stage">
              <img
                key={screenshot}
                src={`./${screenshot}.png`}
                alt={t(
                  `Horizon Harness ${screenshot === "home" ? "终端首页" : screenshot === "conversation" ? "对话界面" : "欢迎界面"}`,
                  `Horizon Harness ${screenshot} screen`,
                )}
                loading="lazy"
                decoding="async"
              />
            </div>
          </div>
        </section>
        <section className="section" id="changelog">
          <div className="section-label">RELEASE JOURNAL</div>
          <div className="section-heading">
            <div>
              <h2>
                <RevealText
                  text={t(
                    "每一步，都在进化。",
                    "A little better, every release.",
                  )}
                />
              </h2>
              <p>
                {t(
                  "新的能力，细节的打磨。Horizon 的每一次向前。",
                  "New capabilities. Thoughtful refinements. Every step forward.",
                )}
              </p>
            </div>
            <input
              aria-label={t("搜索版本", "Search releases")}
              placeholder={t("搜索版本…", "Find a release…")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <ReleaseHistory releases={releases} query={query} en={en} />
          <a className="text-link all-releases" href={`${repo}/releases`}>
            {t("在 GitHub 查看全部版本", "All releases on GitHub")}
            <ArrowUpRight size={16} />
          </a>
        </section>
      </main>
      <footer>
        <a className="brand" href="#">
          <Logo />
          Horizon harness
        </a>
        <p>
          Make it happen.
          <br />
          <span>
            {t(
              "以 MIT 协议开源。为每一个想法，打开新的地平线。",
              "Open source under MIT. A new horizon for every idea.",
            )}
          </span>
        </p>
        <a href={repo}>
          GitHub
          <ArrowUpRight size={14} />
        </a>
      </footer>
      {notice && (
        <div role="status" className="toast" onClick={() => setNotice("")}>
          {notice}
        </div>
      )}
    </>,
  );
}
