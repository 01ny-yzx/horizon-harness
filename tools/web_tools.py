"""Controlled web search and fetch tools."""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any
from urllib.parse import urlparse

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

from core.web_search_provider import resolve_web_search_provider


DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 10
WEB_FETCH_DEFAULT_TIMEOUT_SECONDS = 30
WEB_FETCH_MAX_TIMEOUT_SECONDS = 120
WEB_FETCH_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
WEB_FETCH_FORMATS = {"text", "markdown", "html"}
WEB_FETCH_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
WEB_FETCH_HORIZON_USER_AGENT = "HorizonRuntime"
WEB_FETCH_ACCEPT = {
    "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
    "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
    "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1",
}


if load_dotenv is not None:
    load_dotenv()


def web_search(query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> dict[str, Any]:
    """Search the web through the configured provider and return bounded results."""

    if not isinstance(query, str) or not query.strip():
        return _invalid_web_search_arguments("query must be a non-empty string.")
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= MAX_SEARCH_RESULTS
    ):
        return _invalid_web_search_arguments(
            "max_results must be an integer between 1 and 10."
        )

    provider = resolve_web_search_provider()
    return provider.search(query.strip(), max_results=max_results).to_tool_result()


def _invalid_web_search_arguments(error: str) -> dict[str, Any]:
    return {
        "success": False,
        "status": "failed",
        "error": error,
        "error_code": "invalid_arguments",
        "data": {"code": "invalid_arguments"},
    }


def fetch_url(
    url: str,
    format: str = "markdown",
    timeout: int | float | None = None,
) -> dict[str, Any]:
    """Fetch an HTTP(S) resource and return text, markdown, or HTML."""

    validated = _validate_fetch_arguments(url, format, timeout)
    if validated is None:
        return _fetch_failure(url, "invalid_arguments")
    normalized_url, normalized_format, request_timeout = validated
    if httpx is None or BeautifulSoup is None:
        return _fetch_failure(normalized_url, "missing_dependency")

    try:
        network_result = asyncio.run(
            _fetch_url_network_stage(
                normalized_url,
                normalized_format,
                float(request_timeout),
            )
        )
    except Exception:  # noqa: BLE001
        return _fetch_failure(normalized_url, "response_processing_failed")

    if network_result.get("network_success") is not True:
        return network_result

    try:
        content = bytes(network_result["body"]).decode("utf-8", errors="replace")
        output = _convert_fetch_output(
            content,
            str(network_result["content_type"]),
            normalized_format,
        )
        return {
            "success": True,
            "data": {
                "url": normalized_url,
                "content_type": str(network_result["raw_content_type"]),
                "format": normalized_format,
                "output": output,
            },
        }
    except Exception:  # noqa: BLE001
        return _fetch_failure(normalized_url, "response_processing_failed")


async def _fetch_url_network_stage(
    url: str,
    format: str,
    timeout: float,
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                result = await _fetch_url_response(
                    client,
                    url,
                    format,
                    WEB_FETCH_BROWSER_USER_AGENT,
                    detect_cloudflare_challenge=True,
                )
                if result.get("cloudflare_challenge") is True:
                    result = await _fetch_url_response(
                        client,
                        url,
                        format,
                        WEB_FETCH_HORIZON_USER_AGENT,
                        detect_cloudflare_challenge=False,
                    )
                return result
    except TimeoutError:
        return _fetch_failure(url, "request_timeout")
    except httpx.TimeoutException:
        return _fetch_failure(url, "request_timeout")
    except (httpx.ConnectError, httpx.NetworkError, httpx.RequestError):
        return _fetch_failure(url, "connection_failed")
    except Exception:  # noqa: BLE001
        return _fetch_failure(url, "response_processing_failed")


async def _fetch_url_response(
    client: Any,
    url: str,
    format: str,
    user_agent: str,
    *,
    detect_cloudflare_challenge: bool,
) -> dict[str, Any]:
    async with client.stream(
        "GET",
        url,
        headers=_fetch_headers(format, user_agent),
    ) as response:
        http_status = int(getattr(response, "status_code", 0) or 0)
        if (
            detect_cloudflare_challenge
            and http_status == 403
            and str(response.headers.get("cf-mitigated", "")).strip().lower() == "challenge"
        ):
            return {"cloudflare_challenge": True}
        if not 200 <= http_status < 300:
            return _fetch_failure(url, "http_error", http_status=http_status)

        raw_content_type = str(response.headers.get("content-type", "") or "")
        content_type = raw_content_type.split(";", 1)[0].strip().lower()
        if not _is_supported_fetch_content_type(content_type):
            return _fetch_failure(
                url,
                "unsupported_content_type",
                http_status=http_status,
            )

        declared_length = _valid_content_length(response.headers.get("content-length"))
        if declared_length is not None and declared_length > WEB_FETCH_MAX_RESPONSE_BYTES:
            return _fetch_failure(url, "response_too_large", http_status=http_status)

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > WEB_FETCH_MAX_RESPONSE_BYTES:
                return _fetch_failure(url, "response_too_large", http_status=http_status)
            chunks.append(chunk)

        return {
            "network_success": True,
            "body": b"".join(chunks),
            "raw_content_type": raw_content_type,
            "content_type": content_type,
        }


def _validate_fetch_arguments(
    url: Any,
    format: Any,
    timeout: Any,
) -> tuple[str, str, float | int] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    normalized_url = url.strip()
    try:
        parsed = urlparse(normalized_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if not isinstance(format, str) or format not in WEB_FETCH_FORMATS:
        return None
    if timeout is None:
        return normalized_url, format, WEB_FETCH_DEFAULT_TIMEOUT_SECONDS
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return None
    if not math.isfinite(float(timeout)) or timeout <= 0 or timeout > WEB_FETCH_MAX_TIMEOUT_SECONDS:
        return None
    return normalized_url, format, timeout


def _fetch_headers(format: str, user_agent: str) -> dict[str, str]:
    return {
        "Accept": WEB_FETCH_ACCEPT[format],
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": user_agent,
    }


def _safe_fetch_failure_message(url: Any) -> str:
    value = url.strip() if isinstance(url, str) else "the requested URL"
    value = value[:500] or "the requested URL"
    return f"Unable to fetch {value}"


def _fetch_failure(url: Any, error_code: str, *, http_status: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "url": url.strip() if isinstance(url, str) else "",
        "error_code": error_code,
    }
    if http_status is not None:
        data["http_status"] = http_status
    return {
        "success": False,
        "status": "failed",
        "error": _safe_fetch_failure_message(url),
        "error_code": error_code,
        "data": data,
    }


def _valid_content_length(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _is_supported_fetch_content_type(content_type: str) -> bool:
    if not content_type or content_type.startswith("text/"):
        return True
    return (
        content_type == "application/json"
        or content_type.endswith("+json")
        or content_type == "application/xml"
        or content_type.endswith("+xml")
        or content_type in {"application/javascript", "application/x-javascript"}
    )


def _convert_fetch_output(content: str, content_type: str, format: str) -> str:
    if content_type != "text/html" or format == "html":
        return content
    if format == "text":
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe", "object", "embed"]):
            tag.decompose()
        return " ".join(soup.get_text(" ", strip=True).split())
    return _html_to_markdown(content)


def _html_to_markdown(content: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()

    def render(node: Any) -> str:
        name = str(getattr(node, "name", "") or "").lower()
        if not name:
            return str(node)
        children = "".join(render(child) for child in getattr(node, "children", ()))
        compact = " ".join(children.split())
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return f"\n\n{'#' * int(name[1])} {compact}\n\n"
        if name in {"p", "div", "section", "article"}:
            return f"\n\n{compact}\n\n" if compact else ""
        if name in {"strong", "b"}:
            return f"**{compact}**"
        if name in {"em", "i"}:
            return f"*{compact}*"
        if name == "a":
            href = str(getattr(node, "attrs", {}).get("href") or "")
            return f"[{compact}]({href})" if href else compact
        if name == "code" and getattr(getattr(node, "parent", None), "name", "") != "pre":
            return f"`{children.strip()}`"
        if name == "pre":
            return f"\n\n```\n{node.get_text()}\n```\n\n"
        if name == "blockquote":
            return "\n\n" + "\n".join(f"> {line}" for line in compact.splitlines()) + "\n\n"
        if name == "hr":
            return "\n\n---\n\n"
        if name == "br":
            return "\n"
        if name == "li":
            return compact
        if name in {"ul", "ol"}:
            items = []
            for index, child in enumerate(node.find_all("li", recursive=False), start=1):
                prefix = f"{index}." if name == "ol" else "-"
                items.append(f"{prefix} {render(child)}")
            return "\n\n" + "\n".join(items) + "\n\n"
        return children

    rendered = render(soup)
    lines = [line.rstrip() for line in rendered.splitlines()]
    result = "\n".join(lines)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def extract_urls(text: str) -> list[str]:
    """Extract explicit HTTP(S) URLs from user text."""

    matches = re.findall(r"https?://[^\s<>\]\)\"']+", text or "")
    return [match.rstrip(".,;!?，。；！？") for match in matches]


WEB_TOOLS = {
    "web_search": web_search,
    "fetch_url": fetch_url,
}


WEB_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web for errors, documentation, library usage, and external references through the configured provider.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Maximum result count. Must be between 1 and 10.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch content from an HTTP or HTTPS URL and return it as text, markdown, or HTML. Markdown is the default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "minLength": 1},
                    "format": {
                        "type": "string",
                        "enum": ["text", "markdown", "html"],
                        "default": "markdown",
                    },
                    "timeout": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 120,
                    },
                },
                "required": ["url"],
            },
        },
    },
]
