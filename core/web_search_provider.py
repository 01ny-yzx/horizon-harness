"""Provider abstraction for active web search."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

try:
    import tavily as tavily_module
    from tavily import TavilyClient
except ImportError:  # pragma: no cover
    tavily_module = None  # type: ignore[assignment]
    TavilyClient = None  # type: ignore[assignment]

try:
    import tavily.errors as tavily_errors
except ImportError:  # pragma: no cover
    tavily_errors = None  # type: ignore[assignment]

from core.rate_limit import RateLimiter
from core.unicode_safety import sanitize_unicode
from core.workspace_runtime import get_current_workspace


VALID_PROVIDERS = {"auto", "searxng", "tavily", "disabled"}
DEFAULT_SEARCH_RESULTS = 5
WEB_SEARCH_REQUEST_TIMEOUT_SECONDS = 25
WEB_SEARCH_MAX_RESPONSE_BYTES = 256 * 1024


def _resolve_tavily_exception_type(name: str) -> type[BaseException] | None:
    for module in (tavily_errors, tavily_module):
        candidate = getattr(module, name, None) if module is not None else None
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            return candidate
    return None


TavilyTimeoutError = _resolve_tavily_exception_type("TimeoutError")
TavilyInvalidAPIKeyError = _resolve_tavily_exception_type("InvalidAPIKeyError")
TavilyMissingAPIKeyError = _resolve_tavily_exception_type("MissingAPIKeyError")
TavilyUsageLimitExceededError = _resolve_tavily_exception_type(
    "UsageLimitExceededError"
)
TavilyBadRequestError = _resolve_tavily_exception_type("BadRequestError")
TavilyForbiddenError = _resolve_tavily_exception_type("ForbiddenError")


@dataclass(frozen=True)
class SearchResultItem:
    title: str = ""
    url: str = ""
    snippet: str = ""
    score: Any = None

    def to_dict(self) -> dict[str, Any]:
        data = sanitize_unicode(asdict(self))
        if data.get("score") is None:
            data.pop("score", None)
        return data


@dataclass(frozen=True)
class SearchProviderStatus:
    provider: str
    active_provider: str
    configured_provider: str
    search_available: bool
    fetch_url_available: bool = True
    error_code: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = sanitize_unicode(asdict(self))
        data.setdefault("active_mode", "full_search" if self.search_available else "degraded_fetch_only")
        return data


@dataclass(frozen=True)
class SearchProviderResult:
    provider: str
    query: str
    results: list[SearchResultItem] = field(default_factory=list)
    error_code: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = False

    def to_tool_result(self) -> dict[str, Any]:
        data = {
            "provider": self.provider,
            "query": self.query,
        }
        if self.success:
            data.update(
                {
                    "result_count": len(self.results),
                    "results": [item.to_dict() for item in self.results],
                }
            )
            if self.metadata:
                data["metadata"] = self.metadata
            return {"success": True, "data": sanitize_unicode(data)}
        data["error_code"] = self.error_code
        if self.metadata:
            data["metadata"] = self.metadata
        return {
            "success": False,
            "status": "failed",
            "error": self.reason or self.error_code or "web_search unavailable",
            "error_code": self.error_code,
            "data": sanitize_unicode(data),
        }


class WebSearchProvider(Protocol):
    name: str
    configured_provider: str

    def status(self) -> SearchProviderStatus:
        ...

    def search(self, query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> SearchProviderResult:
        ...


class DisabledProvider:
    name = "disabled"

    def __init__(self, configured_provider: str = "disabled", *, error_code: str = "search_provider_disabled", reason: str = "Active web search is disabled.", metadata: dict[str, Any] | None = None) -> None:
        self.configured_provider = configured_provider
        self.error_code = error_code
        self.reason = reason
        self.metadata = dict(metadata or {})

    def status(self) -> SearchProviderStatus:
        return SearchProviderStatus(
            provider=self.name,
            active_provider=self.name,
            configured_provider=self.configured_provider,
            search_available=False,
            fetch_url_available=True,
            error_code=self.error_code,
            reason=self.reason,
            metadata=self.metadata,
        )

    def search(self, query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> SearchProviderResult:
        del max_results
        status = self.status()
        return SearchProviderResult(
            provider=status.provider,
            query=query,
            error_code=status.error_code,
            reason=status.reason,
            metadata=status.metadata,
            success=False,
        )


class SearXNGProvider:
    name = "searxng"

    def __init__(self, configured_provider: str = "searxng") -> None:
        self.configured_provider = configured_provider
        self.base_url = os.getenv("SEARXNG_BASE_URL", "").strip()

    def status(self) -> SearchProviderStatus:
        return SearchProviderStatus(
            provider=self.name,
            active_provider=self.name,
            configured_provider=self.configured_provider,
            search_available=False,
            fetch_url_available=True,
            error_code="searxng_not_implemented",
            reason="SearXNG provider is configured but search is not implemented in this step.",
            metadata={"base_url_configured": bool(self.base_url)},
        )

    def search(self, query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> SearchProviderResult:
        del max_results
        status = self.status()
        return SearchProviderResult(
            provider=status.provider,
            query=query,
            error_code=status.error_code,
            reason=status.reason,
            metadata=status.metadata,
            success=False,
        )


class TavilyProvider:
    name = "tavily"

    def __init__(self, configured_provider: str = "tavily") -> None:
        self.configured_provider = configured_provider
        self.api_key = os.getenv("TAVILY_API_KEY", "").strip()

    def status(self) -> SearchProviderStatus:
        if not self.api_key:
            return SearchProviderStatus(
                provider=self.name,
                active_provider=self.name,
                configured_provider=self.configured_provider,
                search_available=False,
                fetch_url_available=True,
                error_code="missing_api_key",
                reason="TAVILY_API_KEY is not configured. Active web search is disabled, but URL fetching is available.",
            )
        if TavilyClient is None:
            return SearchProviderStatus(
                provider=self.name,
                active_provider=self.name,
                configured_provider=self.configured_provider,
                search_available=False,
                fetch_url_available=True,
                error_code="missing_dependency",
                reason="TAVILY_API_KEY is configured, but tavily-python is not installed. Please run pip install -r requirements.txt.",
            )
        return SearchProviderStatus(
            provider=self.name,
            active_provider=self.name,
            configured_provider=self.configured_provider,
            search_available=True,
            fetch_url_available=True,
            reason="Tavily search is available.",
        )

    def search(self, query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> SearchProviderResult:
        status = self.status()
        if not status.search_available:
            return SearchProviderResult(
                provider=status.provider,
                query=query,
                error_code=status.error_code,
                reason=status.reason,
                metadata=status.metadata,
                success=False,
            )

        quota = RateLimiter().check_and_increment(get_current_workspace().user_id, "web_search")
        if not quota.get("allowed"):
            return SearchProviderResult(
                provider=self.name,
                query=query,
                error_code="rate_limit_exceeded",
                reason="Daily web search limit exceeded.",
                metadata={"rate_limit": quota},
                success=False,
            )

        try:
            client = TavilyClient(api_key=self.api_key)  # type: ignore[misc]
            response = client.search(
                query=query,
                max_results=max_results,
                timeout=WEB_SEARCH_REQUEST_TIMEOUT_SECONDS,
            )
            response_boundary_error = _provider_response_boundary_error(response)
            if response_boundary_error:
                return SearchProviderResult(
                    provider=self.name,
                    query=query,
                    error_code=response_boundary_error,
                    reason=_safe_web_search_failure_message(query),
                    success=False,
                )
            response_error = _provider_response_error(response)
            if response_error:
                return SearchProviderResult(
                    provider=self.name,
                    query=query,
                    error_code="provider_response_invalid",
                    reason=_safe_web_search_failure_message(query),
                    metadata=response_error,
                    success=False,
                )
            raw_results = response["results"]
            results = [
                _normalize_search_result(item)
                for item in raw_results[:max_results]
            ]
            return SearchProviderResult(
                provider=self.name,
                query=query,
                results=results,
                success=True,
            )
        except Exception as exc:  # noqa: BLE001
            error_code = _provider_exception_code(exc)
            return SearchProviderResult(
                provider=self.name,
                query=query,
                error_code=error_code,
                reason=_safe_web_search_failure_message(query),
                success=False,
            )


def resolve_web_search_provider() -> WebSearchProvider:
    configured = _configured_provider()
    if configured not in VALID_PROVIDERS:
        return DisabledProvider(
            configured_provider=configured,
            error_code="provider_invalid",
            reason=f"Invalid WEB_SEARCH_PROVIDER value: {configured}.",
            metadata={"valid_providers": sorted(VALID_PROVIDERS)},
        )
    if configured == "disabled":
        return DisabledProvider(configured_provider=configured)
    if configured == "searxng":
        return SearXNGProvider(configured_provider=configured)
    if configured == "tavily":
        return TavilyProvider(configured_provider=configured)
    return _auto_provider()


def get_web_search_provider_status() -> SearchProviderStatus:
    return resolve_web_search_provider().status()


def _auto_provider() -> WebSearchProvider:
    skipped: list[dict[str, str]] = [
        {"provider": "searxng", "reason": "searxng_not_implemented"},
    ]
    if os.getenv("TAVILY_API_KEY", "").strip():
        provider = TavilyProvider(configured_provider="auto")
        provider_status = provider.status()
        if provider_status.search_available or provider_status.error_code in {"missing_dependency"}:
            return provider
        skipped.append({"provider": "tavily", "reason": provider_status.error_code or "unavailable"})
    else:
        skipped.append({"provider": "tavily", "reason": "missing_api_key"})
    return DisabledProvider(
        configured_provider="auto",
        error_code="search_provider_disabled",
        reason="No implemented web search provider is available.",
        metadata={"skipped_candidates": skipped},
    )


def _configured_provider() -> str:
    return str(os.getenv("WEB_SEARCH_PROVIDER", "auto") or "auto").strip().lower()


def _provider_response_error(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}
    results = response.get("results")
    if not isinstance(results, list):
        return {"results_type": type(results).__name__}
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            return {
                "invalid_result_index": index,
                "result_type": type(item).__name__,
            }
    return {}


def _provider_response_boundary_error(response: Any) -> str:
    try:
        serialized = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return "provider_response_invalid"
    if len(serialized) > WEB_SEARCH_MAX_RESPONSE_BYTES:
        return "provider_response_too_large"
    return ""


def _exception_types(
    *candidates: type[BaseException] | None,
) -> tuple[type[BaseException], ...]:
    return tuple(candidate for candidate in candidates if candidate is not None)


def _provider_exception_code(exc: Exception) -> str:
    classifications = (
        (_exception_types(TavilyTimeoutError), "provider_timeout"),
        (
            _exception_types(
                TavilyInvalidAPIKeyError,
                TavilyMissingAPIKeyError,
            ),
            "provider_auth_error",
        ),
        (
            _exception_types(TavilyUsageLimitExceededError),
            "provider_rate_limited",
        ),
        (_exception_types(TavilyBadRequestError), "provider_bad_request"),
        (_exception_types(TavilyForbiddenError), "provider_forbidden"),
    )
    for exception_types, error_code in classifications:
        if exception_types and isinstance(exc, exception_types):
            return error_code
    return "provider_error"


def _safe_web_search_failure_message(query: str) -> str:
    safe_query = str(sanitize_unicode(query))[:200]
    return f"Unable to search the web for {safe_query}"


def _normalize_search_result(item: Any) -> SearchResultItem:
    if not isinstance(item, dict):
        return SearchResultItem(snippet=str(item))
    return SearchResultItem(
        title=str(item.get("title", "")),
        url=str(item.get("url", "")),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        score=item.get("score"),
    )
