import { revealContent } from "./revealContent";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpRight } from "lucide-react";
import type { Release } from "./App";

export default function ReleaseHistory({
  releases,
  query,
  en,
}: {
  releases: Release[];
  query: string;
  en: boolean;
}) {
  const t = (zh: string, english: string) => (en ? english : zh);
  const items = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const tags = releases.filter((r) =>
      r.tag_name.toLowerCase().includes(needle),
    );
    const matches = tags.length
      ? tags
      : releases.filter((r) => r.body.toLowerCase().includes(needle));
    return matches.slice().reverse();
  }, [releases, query]);
  const [tag, setTag] = useState<string | null>(null);
  const found = items.findIndex((r) => r.tag_name === tag);
  const active = found < 0 ? items.length - 1 : found;
  const r = items[active];
  const wheel = useRef<HTMLDivElement>(null);
  const details = useRef<HTMLDivElement>(null);
  const chooseRef = useRef<(index: number) => void>(() => {});
  const motion = useRef({ position: active, velocity: 0, target: active });
  const detailAnimation = useRef<Animation>();
  const previousTag = useRef<string>();
  chooseRef.current = (index) => {
    const next = Math.max(0, Math.min(items.length - 1, index));
    if (next !== active && items[next]) setTag(items[next].tag_name);
  };

  useEffect(() => {
    if (!r || previousTag.current === r.tag_name) return;
    const initial = previousTag.current === undefined;
    previousTag.current = r.tag_name;
    detailAnimation.current?.cancel();
    if (!initial) {
      detailAnimation.current = details.current?.animate(
        [
          { opacity: 0.2, transform: "translateY(10px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        { duration: 380, easing: "cubic-bezier(.2,.7,.3,1)" },
      );
    }
    return () => detailAnimation.current?.cancel();
  }, [r?.tag_name]);

  useEffect(() => {
    const el = wheel.current;
    if (!el || !items.length) return;
    let frame = 0,
      previous = 0;
    let width = el.clientWidth,
      height = el.clientHeight;
    motion.current = { position: active, target: active, velocity: 0 };
    const paint = () => {
      const radius = Math.min(155, width * 0.45),
        cx = width - 12,
        cy = height / 2;
      el.querySelectorAll<HTMLElement>(".release-node").forEach(
        (node, index) => {
          const delta = index - motion.current.position,
            distance = Math.abs(delta);
          const angle = Math.max(-3, Math.min(3, delta)) * 0.68;
          node.style.transform = `translate(${cx - Math.cos(angle) * radius - node.offsetWidth - 12}px, ${cy + Math.sin(angle) * radius - 22}px) scale(${1.06 - Math.min(distance, 2.6) * 0.085})`;
          node.style.opacity = `${Math.max(0, Math.min(1, (2.7 - distance) / 0.7)) * (1 - Math.min(distance, 2) * 0.22)}`;
          node.style.visibility = distance > 2.7 ? "hidden" : "visible";
          node.style.pointerEvents = distance > 2.45 ? "none" : "auto";
        },
      );
    };
    const geometry = () => {
      width = el.clientWidth;
      height = el.clientHeight;
      const radius = Math.min(155, width * 0.45);
      const path = Array.from({ length: 81 }, (_, i) => {
        const angle = -1.48 + (i * 2.96) / 80;
        return `${i ? "L" : "M"}${width - 12 - Math.cos(angle) * radius},${height / 2 + Math.sin(angle) * radius}`;
      }).join(" ");
      el.querySelector("path")?.setAttribute("d", path);
      paint();
    };
    const tick = (now: number) => {
      const dt = Math.min((now - (previous || now - 16)) / 1000, 0.032);
      previous = now;
      const m = motion.current;
      m.velocity += (180 * (m.target - m.position) - 23 * m.velocity) * dt;
      m.position += m.velocity * dt;
      if (
        Math.abs(m.target - m.position) < 0.0006 &&
        Math.abs(m.velocity) < 0.008
      ) {
        m.position = m.target;
        m.velocity = 0;
        frame = 0;
        paint();
        return;
      }
      paint();
      frame = requestAnimationFrame(tick);
    };
    const start = () => {
      if (!frame) {
        previous = 0;
        frame = requestAnimationFrame(tick);
      }
    };
    // Selection changes update the target without resetting spring momentum.
    const observer = new MutationObserver(() => {
      const selected = el.querySelector<HTMLElement>('[aria-pressed="true"]');
      if (selected) {
        motion.current.target = Number(selected.dataset.index);
        start();
      }
    });
    observer.observe(el, {
      attributes: true,
      subtree: true,
      attributeFilter: ["aria-pressed"],
    });
    const resize = new ResizeObserver(geometry);
    resize.observe(el);
    geometry();
    let accumulated = 0,
      lastWheel = 0,
      lastSwitch = -Infinity,
      pointerY: number | null = null;
    const onWheel = (event: WheelEvent) => {
      if (
        event.ctrlKey ||
        Math.abs(event.deltaX) > Math.abs(event.deltaY) ||
        !event.deltaY
      )
        return;
      event.preventDefault();
      const now = performance.now();
      if (now - lastWheel > 180) accumulated = 0;
      const delta =
        event.deltaY *
        (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1);
      if (Math.sign(delta) !== Math.sign(accumulated)) accumulated = 0;
      accumulated += delta;
      lastWheel = now;
      if (Math.abs(accumulated) >= 65 && now - lastSwitch >= 240) {
        chooseRef.current(motion.current.target + Math.sign(accumulated));
        accumulated = 0;
        lastSwitch = now;
      }
    };
    const down = (event: PointerEvent) => {
      if (event.pointerType !== "mouse") {
        pointerY = event.clientY;
        el.setPointerCapture(event.pointerId);
      }
    };
    const up = (event: PointerEvent) => {
      if (pointerY !== null && Math.abs(event.clientY - pointerY) > 45)
        chooseRef.current(
          motion.current.target + (event.clientY < pointerY ? 1 : -1),
        );
      pointerY = null;
    };
    const cancel = () => {
      pointerY = null;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", cancel);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      observer.disconnect();
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", cancel);
    };
  }, [items]);

  if (!r)
    return (
      <p className="release-empty">
        {t("没有匹配的版本。", "No matching releases.")}
      </p>
    );
  return revealContent(
    <div
      className="release-history"
      role="region"
      aria-label={t("版本时间轮盘", "Release timeline")}
      onKeyDown={(event) => {
        if (
          event.key === "ArrowUp" ||
          event.key === "ArrowDown" ||
          event.key === "Home" ||
          event.key === "End"
        ) {
          event.preventDefault();
          chooseRef.current(
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? items.length - 1
                : active + (event.key === "ArrowUp" ? -1 : 1),
          );
        }
      }}
    >
      <div className="release-copy">
        <div
          ref={details}
          aria-live="polite"
          aria-atomic="true"
          className="release-detail"
        >
          <div className="release-eyebrow">
            <span>{t("版本记录", "RELEASE NOTES")}</span>
            {r.tag_name === releases[0]?.tag_name && (
              <span className="latest">{t("最新版本", "Latest")}</span>
            )}
          </div>
          <div className="release-version">{r.tag_name}</div>
          <time dateTime={r.published_at}>
            {new Date(r.published_at).toLocaleDateString(
              en ? "en-US" : "zh-CN",
            )}
          </time>
          <h3>
            {r.tag_name === "v1.0.3"
              ? t(
                  "让每一段会话，都井然有序。",
                  "A little more room to move forward.",
                )
              : r.tag_name === "v1.0.2"
                ? t(
                    "更可靠的连接，更完整的体验。",
                    "Better connected. Ready for more.",
                  )
                : r.tag_name === "v1.0.1"
                  ? t("更新体验，迈出下一步。", "A new way to stay up to date.")
                  : r.tag_name === "v1.0.0"
                    ? t("你好，Horizon。", "Hello, Horizon.")
                    : t(
                        "新的进展，持续发生。",
                        "More progress. More possibilities.",
                      )}
          </h3>
          <p>
            {r.tag_name === "v1.0.3"
              ? t(
                  "新增历史会话删除，键盘选择、确认后删除，并同步清理标签与排队内容。官网也带来了逐字浮现、界面预览与竖向版本轮盘。",
                  "Delete saved conversations with keyboard selection and explicit confirmation. Tabs and queued messages are cleaned up together. The new website adds character reveals, screen previews, and an arc-shaped release timeline.",
                )
              : r.tag_name === "v1.0.2"
                ? t(
                    "补齐 SOCKS 代理支持，改善联网检查的稳定性。包含完整的版本更新流程、底栏信息调整，以及 Windows 安装兼容修复。",
                    "SOCKS proxy support and reliable update checks. Includes the complete updater, a refined footer, and Windows installation fixes.",
                  )
                : r.tag_name === "v1.0.1"
                  ? t(
                      "每次启动检查更新，通过 /update 确认升级。工作目录与版本信息，一眼可见。",
                      "Check for updates on startup and upgrade through /update. Working directory and version, at a glance.",
                    )
                  : r.tag_name !== "v1.0.0"
                    ? r.body
                        .split("\n")
                        .filter((line) => line.startsWith("- "))
                        .slice(0, 3)
                        .join(" ")
                        .replace(/^- /, "")
                        .slice(0, 380) ||
                      t(
                        "在 GitHub 查看完整更新说明。",
                        "View the full release notes on GitHub.",
                      )
                    : t(
                        "Horizon Harness 的首个正式版本。流式对话、会话管理、模型配置与任务排队，来到你的终端。",
                        "The first stable release. Streaming conversations, persistent sessions, model configuration and queued tasks in your terminal.",
                      )}
          </p>
          <a className="small-link" href={r.html_url}>
            {t("完整更新说明", "Full release notes")}
            <ArrowUpRight size={13} />
          </a>
        </div>
        <div className="release-controls">
          <button
            aria-label={t("上一个版本", "Older release")}
            disabled={active <= 0}
            onClick={() => chooseRef.current(active - 1)}
          >
            <ArrowUp size={18} />
          </button>
          <button
            aria-label={t("下一个版本", "Newer release")}
            disabled={active >= items.length - 1}
            onClick={() => chooseRef.current(active + 1)}
          >
            <ArrowDown size={18} />
          </button>
          <span>
            {String(active + 1).padStart(2, "0")}{" "}
            <i>/ {String(items.length).padStart(2, "0")}</i>
          </span>
        </div>
      </div>
      <div className="release-wheel-wrap">
        <div
          ref={wheel}
          className="release-wheel"
          tabIndex={0}
          aria-label={t(
            "滚轮或上下方向键切换版本",
            "Scroll or use arrow keys to switch releases",
          )}
        >
          <svg aria-hidden="true">
            <path fill="none" />
          </svg>
          {items.map((release, index) => (
            <button
              key={release.tag_name}
              className="release-node"
              data-index={index}
              aria-pressed={index === active}
              tabIndex={index === active ? 0 : -1}
              onClick={() => chooseRef.current(index)}
            >
              {release.tag_name}
              <span aria-hidden="true" />
            </button>
          ))}
        </div>
        <span className="release-wheel-hint">
          {t("滚动，探索每一次向前", "SCROLL THROUGH THE JOURNEY")}
        </span>
      </div>
    </div>,
  );
}
