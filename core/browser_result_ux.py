"""Structured Browser MCP result helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from config.settings import PROJECT_ROOT, settings


MAX_LINKS = 100
MAX_LINK_TEXT_CHARS = 120
SUMMARY_MAX_CHARS = 1200
SHORT_TEXT_THRESHOLD = 80


@dataclass(frozen=True)
class BrowserPageSnapshot:
    """Bounded page data collected from a browser page."""

    requested_url: str
    final_url: str
    title: str
    meta_description: str
    headings: list[str]
    text: str
    links: list[dict[str, Any]]


def extract_page_metadata(page: Any, requested_url: str, timeout_ms: int) -> BrowserPageSnapshot:
    """Collect page title, metadata, visible text and links with fallbacks."""

    final_url = str(getattr(page, "url", "") or requested_url)
    title = _safe_call(lambda: str(page.title() or "").strip())
    meta_description = _first_non_empty(
        [
            _safe_text(page, 'meta[name="description"]', "content", timeout_ms),
            _safe_text(page, 'meta[property="og:description"]', "content", timeout_ms),
        ]
    )
    headings = _extract_headings(page)
    text = _extract_visible_text(page, timeout_ms)
    links = extract_links(page, final_url, limit=MAX_LINKS + 1)
    return BrowserPageSnapshot(
        requested_url=requested_url,
        final_url=final_url,
        title=title,
        meta_description=meta_description,
        headings=headings,
        text=text,
        links=links,
    )


def format_browser_extract_result(snapshot: BrowserPageSnapshot, max_chars: int, *, fetched_at: str | None = None) -> dict[str, Any]:
    """Build the structured browser_extract_text payload."""

    bounded_text, truncated = truncate_browser_text(snapshot.text, max_chars)
    quality = classify_extraction_quality(bounded_text)
    content_warnings = []
    if quality == "empty":
        content_warnings.append("No readable page body text was extracted.")
    elif quality == "short":
        content_warnings.append("Readable page body text is short.")
    summary, summary_source = summarize_page_text(
        title=snapshot.title,
        meta_description=snapshot.meta_description,
        headings=snapshot.headings,
        text=bounded_text,
        extraction_quality=quality,
    )
    links = snapshot.links[:MAX_LINKS]
    internal_count = sum(1 for link in links if link.get("kind") == "internal")
    external_count = sum(1 for link in links if link.get("kind") == "external")
    return {
        "url": snapshot.requested_url,
        "final_url": snapshot.final_url,
        "title": snapshot.title,
        "meta_description": snapshot.meta_description,
        "headings": snapshot.headings,
        "text": bounded_text,
        "text_preview": bounded_text[:1000],
        "text_excerpt": bounded_text[:1000],
        "text_length": len(bounded_text),
        "original_text_length": len(snapshot.text),
        "truncated": truncated,
        "summary": summary,
        "summary_source": summary_source,
        "links": links,
        "links_count": len(links),
        "internal_links_count": internal_count,
        "external_links_count": external_count,
        "links_truncated": len(snapshot.links) > len(links),
        "extraction_quality": quality,
        "content_warnings": content_warnings,
        "read_method": "browser_extract_text",
        "method": "browser_extract_text",
        "source_type": "browser_page",
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


def extract_links(page: Any, base_url: str, limit: int = MAX_LINKS) -> list[dict[str, Any]]:
    """Extract bounded, de-duplicated HTTP(S) links from a page."""

    raw_links = _safe_call(
        lambda: page.locator("a").evaluate_all(
            """elements => elements.map(a => ({
                text: (a.innerText || a.textContent || '').trim(),
                href: a.getAttribute('href') || a.href || ''
            }))"""
        ),
        default=[],
    )
    if not isinstance(raw_links, list):
        return []

    base_host = (urlparse(base_url).hostname or "").lower()
    seen: set[str] = set()
    links: list[dict[str, Any]] = []
    for item in raw_links:
        if len(links) >= max(1, limit):
            break
        if not isinstance(item, dict):
            continue
        href = normalize_link_href(str(item.get("href") or ""), base_url)
        if not href or href in seen:
            continue
        seen.add(href)
        host = (urlparse(href).hostname or "").lower()
        links.append(
            {
                "text": _clip(_normalize_whitespace(str(item.get("text") or "")), MAX_LINK_TEXT_CHARS),
                "href": href,
                "domain": host,
                "kind": "internal" if host == base_host else "external",
            }
        )
    return links


def format_links_result(snapshot: BrowserPageSnapshot, *, limit: int = MAX_LINKS, fetched_at: str | None = None) -> dict[str, Any]:
    """Build browser_list_links result fields."""

    links = snapshot.links[: max(1, limit)]
    internal_count = sum(1 for link in links if link.get("kind") == "internal")
    external_count = sum(1 for link in links if link.get("kind") == "external")
    return {
        "title": snapshot.title,
        "url": snapshot.requested_url,
        "final_url": snapshot.final_url,
        "links": links,
        "links_count": len(links),
        "internal_links_count": internal_count,
        "external_links_count": external_count,
        "links_truncated": len(snapshot.links) > len(links),
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


def summarize_page_text(
    *,
    title: str,
    meta_description: str,
    headings: list[str],
    text: str,
    extraction_quality: str,
) -> tuple[str, str]:
    """Create a deterministic short summary without calling an LLM."""

    if extraction_quality == "empty":
        base = title or meta_description or "Page"
        return _clip(f"{base}: no enough readable body text was extracted.", SUMMARY_MAX_CHARS), "empty"

    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    if meta_description:
        parts.append(f"Description: {meta_description}")
    useful_headings = [heading for heading in headings if heading][:5]
    if useful_headings:
        parts.append("Headings: " + " | ".join(useful_headings))
    paragraphs = _paragraphs(text)[:3]
    if paragraphs:
        parts.append("Text: " + " ".join(paragraphs))
    if not parts:
        return "No enough readable body text was extracted.", "empty"
    return _clip(" ".join(parts), SUMMARY_MAX_CHARS), "title_meta_headings_text"


def truncate_browser_text(text: str, max_chars: int | None = None) -> tuple[str, bool]:
    """Normalize and bound browser text."""

    normalized = _normalize_whitespace(text)
    try:
        requested = int(max_chars or settings.browser_max_text_chars)
    except (TypeError, ValueError):
        requested = settings.browser_max_text_chars
    limit = max(1, min(requested, max(500, int(settings.browser_max_text_chars or 12000))))
    return normalized[:limit], len(normalized) > limit


def classify_extraction_quality(text: str) -> str:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return "empty"
    if len(normalized) < SHORT_TEXT_THRESHOLD:
        return "short"
    return "ok"


def safe_screenshot_path(final_url: str, screenshot_dir: str | Path | None = None, *, now: datetime | None = None) -> dict[str, str]:
    """Return a safe screenshot path inside the configured browser artifact directory."""

    base_dir = _safe_screenshot_dir(screenshot_dir)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    host = urlparse(final_url).hostname or "page"
    safe_host = re.sub(r"[^a-zA-Z0-9_.-]+", "_", host)[:60].strip("._-") or "page"
    filename = f"{stamp}_{safe_host}.png"
    path = base_dir / filename
    return {"screenshot_path": str(path), "screenshot_dir": str(base_dir), "filename": filename}


def normalize_link_href(href: str, base_url: str) -> str:
    href = str(href or "").strip()
    if not href:
        return ""
    lowered = href.lower()
    if lowered.startswith(("javascript:", "mailto:", "tel:", "data:", "blob:")):
        return ""
    joined = urljoin(base_url, href)
    parsed = urlparse(joined)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    parsed = parsed._replace(fragment="")
    return urlunparse(parsed)


def _extract_visible_text(page: Any, timeout_ms: int) -> str:
    selectors = ["main", "article", '[role="main"]', "body"]
    for selector in selectors:
        text = _safe_inner_text(page, selector, timeout_ms)
        if text:
            return text
    fallback_parts = []
    for selector in ["p", "li", "table"]:
        fallback_parts.extend(_safe_text_list(page, selector))
    fallback = _normalize_whitespace("\n".join(fallback_parts))
    if fallback:
        return fallback
    return _safe_inner_text(page, "body", timeout_ms)


def _extract_headings(page: Any) -> list[str]:
    values = _safe_call(
        lambda: page.locator("h1, h2").evaluate_all(
            """elements => elements.slice(0, 20).map(el => (el.innerText || el.textContent || '').trim())"""
        ),
        default=[],
    )
    if not isinstance(values, list):
        return []
    headings: list[str] = []
    for value in values:
        text = _clip(_normalize_whitespace(str(value or "")), 160)
        if text and text not in headings:
            headings.append(text)
    return headings


def _safe_inner_text(page: Any, selector: str, timeout_ms: int) -> str:
    return _normalize_whitespace(_safe_call(lambda: page.locator(selector).inner_text(timeout=timeout_ms), default=""))


def _safe_text(page: Any, selector: str, attribute: str, timeout_ms: int) -> str:
    return _normalize_whitespace(_safe_call(lambda: page.locator(selector).first.get_attribute(attribute, timeout=timeout_ms), default=""))


def _safe_text_list(page: Any, selector: str) -> list[str]:
    values = _safe_call(
        lambda: page.locator(selector).evaluate_all(
            """elements => elements.slice(0, 80).map(el => (el.innerText || el.textContent || '').trim())"""
        ),
        default=[],
    )
    if not isinstance(values, list):
        return []
    return [_normalize_whitespace(str(value or "")) for value in values if _normalize_whitespace(str(value or ""))]


def _safe_screenshot_dir(screenshot_dir: str | Path | None) -> Path:
    raw = Path(str(screenshot_dir or settings.browser_screenshot_dir or "browser_artifacts"))
    candidate = raw if raw.is_absolute() else PROJECT_ROOT / raw
    project_artifacts = (PROJECT_ROOT / "browser_artifacts").resolve()
    resolved = candidate.resolve()
    if resolved == project_artifacts or project_artifacts in resolved.parents:
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    configured = (PROJECT_ROOT / settings.browser_screenshot_dir).resolve()
    if resolved == configured or configured in resolved.parents:
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    project_artifacts.mkdir(parents=True, exist_ok=True)
    return project_artifacts


def _paragraphs(text: str) -> list[str]:
    paragraphs = []
    for line in _normalize_whitespace(text).splitlines():
        item = line.strip()
        if len(item) >= 20:
            paragraphs.append(item)
    return paragraphs


def _normalize_whitespace(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 15)].rstrip() + "...[truncated]"


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _safe_call(func: Any, default: Any = "") -> Any:
    try:
        value = func()
    except Exception:  # noqa: BLE001 - page extraction must fall back.
        return default
    return default if value is None else value
