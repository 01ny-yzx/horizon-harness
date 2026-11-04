import { revealContent } from "./revealContent";
import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle } from "lucide-react";

export default function AnimatedTerminal({
  en,
  version,
}: {
  en: boolean;
  version: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [elapsed, setElapsed] = useState(12000);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);
  useEffect(() => {
    let visible = true,
      clock = 0,
      previous = performance.now();
    setElapsed(0);
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      previous = performance.now();
    });
    if (ref.current) observer.observe(ref.current);
    const timer = window.setInterval(() => {
      const now = performance.now(),
        delta = Math.min(now - previous, 160);
      previous = now;
      if (document.hidden || !visible || pausedRef.current) return;
      clock = (clock + delta) % 14000;
      setElapsed(clock);
    }, 80);
    return () => {
      clearInterval(timer);
      observer.disconnect();
    };
  }, [en]);
  const t = (zh: string, english: string) => (en ? english : zh);
  const prompt = t(
    "把这个想法，变成一个可以运行的项目。",
    "Turn this idea into something that works.",
  );
  const answer = t("准备好了。让想法发生。", "Ready. Let’s make it happen.");
  const inputEnd = 3000,
    firstEnd = 5700,
    secondStart = 6400,
    secondEnd = 8600;
  const count = Math.floor(
    (Math.max(0, elapsed - 600) / 2400) * Array.from(prompt).length,
  );
  const typed = Array.from(prompt).slice(0, count).join("");
  const typedAnswer = Array.from(answer)
    .slice(
      0,
      Math.floor(
        (Math.max(0, elapsed - 9000) / 1600) * Array.from(answer).length,
      ),
    )
    .join("");
  const stage =
    elapsed < inputEnd
      ? 0
      : elapsed < firstEnd
        ? 1
        : elapsed < secondStart
          ? 2
          : elapsed < secondEnd
            ? 3
            : elapsed < 9000
              ? 4
              : 5;
  const tool = (show: boolean, done: boolean, text: string, detail: string) => (
    <div className={`tool-line demo-line ${show ? "is-shown" : ""}`}>
      <span className="tool-icon">
        {done ? (
          <Check size={14} />
        ) : (
          <LoaderCircle size={14} className="tool-spinner" />
        )}
      </span>
      {text}
      <span>{done ? detail : t("进行中", "working")}</span>
    </div>
  );
  return revealContent(
    <div
      className="terminal-content"
      ref={ref}
      aria-label={t(
        "终端工作流程动画演示",
        "Animated terminal workflow demonstration",
      )}
    >
      <div className="terminal-nav">
        <strong>Horizon harness</strong>
        <span>{version}</span>
      </div>
      <div aria-hidden="true" className="demo-sequence">
        <p className="prompt">
          <span>❯</span> {typed}
          <span
            className={`typing-block input-caret ${stage === 0 ? "is-shown" : ""}`}
          />
        </p>
        <p className={`demo-line demo-intro ${stage >= 1 ? "is-shown" : ""}`}>
          {t(
            "从一个清晰的目标开始。接下来的工作，交给我们一起完成。",
            "Start with a clear goal. Let’s take it from here.",
          )}
        </p>
        {tool(
          stage >= 1,
          stage >= 2,
          t("读取项目结构", "Read project structure"),
          "12 files",
        )}
        {tool(
          stage >= 3,
          stage >= 4,
          t("实现功能并验证", "Build and verify"),
          "done",
        )}
        <p className={`answer demo-line ${stage >= 5 ? "is-shown" : ""}`}>
          {typedAnswer}
          <span className="typing-block" />
        </p>
      </div>
      <p className="sr-only">
        {t(
          "演示：输入任务，读取项目结构，实现功能并验证，最后给出回答。",
          "Demo: enter a task, read project files, build and verify, then receive a response.",
        )}
      </p>
      <div className="terminal-input">
        ❯ <span>{t("你的下一步想法…", "Your next idea…")}</span>
      </div>
      <div className="terminal-footer">
        <span>◆ &nbsp; {t("你选择的模型", "Your model")} · ~/workspace</span>
        <button
          className="demo-toggle"
          onClick={() => setPaused(!paused)}
          aria-label={
            paused
              ? t("播放演示动画", "Play animation")
              : t("暂停演示动画", "Pause animation")
          }
        >
          {paused ? t("继续播放 ▷", "PLAY ▷") : t("演示 ⏸", "DEMO ⏸")}
        </button>
      </div>
    </div>,
  );
}
