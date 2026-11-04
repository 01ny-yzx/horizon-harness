import {
  Children,
  cloneElement,
  Fragment,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";
import RevealText from "./RevealText";

/** Decorate text through React so labels and dynamic values retain React ownership. */
export function revealContent(content: ReactNode): ReactNode {
  return Children.map(content, (child) => {
    if (typeof child === "string" || typeof child === "number") {
      const text = String(child);
      return text.trim() ? <RevealText key={text} text={text} /> : child;
    }
    if (!isValidElement(child)) return child;
    const element = child as ReactElement<{
      children?: ReactNode;
      className?: string;
      "aria-hidden"?: boolean | "true" | "false";
    }>;
    const { type, props } = element;
    if (type !== Fragment && typeof type !== "string") return child;
    if (
      typeof type === "string" &&
      [
        "header",
        "h1",
        "svg",
        "input",
        "textarea",
        "option",
        "script",
        "style",
      ].includes(type)
    )
      return child;
    if (
      props["aria-hidden"] === true ||
      props["aria-hidden"] === "true" ||
      /(?:^|\s)(?:sr-only|demo-sequence)(?:\s|$)/.test(props.className || "")
    )
      return child;
    return cloneElement(element, undefined, revealContent(props.children));
  });
}
