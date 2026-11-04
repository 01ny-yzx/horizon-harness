import { useEffect, useRef, type CSSProperties } from "react";

let observer: IntersectionObserver | undefined;
let observedCount = 0;

/** Keep the complete label accessible while visual characters enter in order. */
export default function RevealText({
  text,
  className = "",
  delay = 0,
}: {
  text: string;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (!observer)
      observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add("is-revealed");
            observer?.unobserve(entry.target);
          });
        },
        { threshold: 0.08 },
      );
    node.classList.remove("is-revealed");
    observer.observe(node);
    observedCount++;
    return () => {
      observer?.unobserve(node);
      observedCount--;
      if (!observedCount) {
        observer?.disconnect();
        observer = undefined;
      }
    };
  }, [text]);
  let index = 0;
  const step = Math.min(45, 650 / Math.max(1, Array.from(text).length));
  return (
    <span ref={ref} className={`reveal-text scroll-reveal ${className}`}>
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        {(
          text.match(
            /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]|[^\s\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]+|\s+/gu,
          ) || []
        ).map((word, wordIndex) =>
          /^\s+$/.test(word) ? (
            word
          ) : (
            <span className="reveal-word" key={wordIndex}>
              {Array.from(word).map((character, characterIndex) => (
                <span
                  className="reveal-char"
                  key={characterIndex}
                  style={
                    {
                      "--char-delay": `${delay + index++ * step}ms`,
                    } as CSSProperties
                  }
                >
                  {character}
                </span>
              ))}
            </span>
          ),
        )}
      </span>
    </span>
  );
}
