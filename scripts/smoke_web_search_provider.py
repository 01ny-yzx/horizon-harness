from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.web_search_provider import get_web_search_provider_status, resolve_web_search_provider
from tools.web_tools import fetch_url, web_search


class EnvPatch:
    def __init__(self, **values: str | None) -> None:
        self.values = values
        self.previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def __exit__(self, *_: object) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_disabled_provider() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="disabled", TAVILY_API_KEY=None, SEARXNG_BASE_URL=None):
        status = get_web_search_provider_status().to_dict()
        assert status["active_provider"] == "disabled"
        assert status["search_available"] is False
        assert status["fetch_url_available"] is True
        result = web_search("python docs", max_results=2)
    assert result["success"] is False
    assert result["error_code"] == "search_provider_disabled"
    assert result["data"]["provider"] == "disabled"
    assert "active_provider" not in result["data"]


def test_tavily_without_key() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="tavily", TAVILY_API_KEY=None):
        status = get_web_search_provider_status().to_dict()
        assert status["active_provider"] == "tavily"
        assert status["configured_provider"] == "tavily"
        assert status["search_available"] is False
        result = web_search("python docs")
    assert result["success"] is False
    assert result["error_code"] == "missing_api_key"
    assert result["data"]["provider"] == "tavily"
    assert "search_available" not in result["data"]


def test_auto_without_any_provider() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="auto", TAVILY_API_KEY=None, SEARXNG_BASE_URL=None):
        status = get_web_search_provider_status().to_dict()
    assert status["configured_provider"] == "auto"
    assert status["active_provider"] == "disabled"
    assert status["search_available"] is False
    skipped = status.get("metadata", {}).get("skipped_candidates", [])
    assert any(item.get("provider") == "searxng" for item in skipped)


def test_auto_with_tavily_key_selects_tavily() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="auto", TAVILY_API_KEY="tvly-test-key", SEARXNG_BASE_URL=None):
        provider = resolve_web_search_provider()
        status = get_web_search_provider_status().to_dict()
    assert provider.name == "tavily"
    assert status["configured_provider"] == "auto"
    assert status["active_provider"] == "tavily"


def test_searxng_placeholder_no_fallback() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="searxng", TAVILY_API_KEY="tvly-test-key", SEARXNG_BASE_URL="http://localhost:8888"):
        status = get_web_search_provider_status().to_dict()
        result = web_search("python docs")
    assert status["configured_provider"] == "searxng"
    assert status["active_provider"] == "searxng"
    assert status["search_available"] is False
    assert result["success"] is False
    assert result["error_code"] == "searxng_not_implemented"
    assert result["data"]["provider"] == "searxng"


def test_invalid_provider_is_structured() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="bogus", TAVILY_API_KEY=None):
        status = get_web_search_provider_status().to_dict()
        result = web_search("python docs")
    assert status["configured_provider"] == "bogus"
    assert status["active_provider"] == "disabled"
    assert status["error_code"] == "provider_invalid"
    assert result["success"] is False
    assert result["error_code"] == "provider_invalid"


def test_fetch_url_independent_from_provider() -> None:
    with EnvPatch(WEB_SEARCH_PROVIDER="bogus", TAVILY_API_KEY=None):
        result = fetch_url("ftp://example.com/file.txt")
    assert result["success"] is False
    assert "provider_invalid" not in str(result)
    assert "search_provider" not in str(result)
    assert "active_provider" not in result.get("data", {})


def main() -> None:
    test_disabled_provider()
    test_tavily_without_key()
    test_auto_without_any_provider()
    test_auto_with_tavily_key_selects_tavily()
    test_searxng_placeholder_no_fallback()
    test_invalid_provider_is_structured()
    test_fetch_url_independent_from_provider()
    print("smoke_web_search_provider ok")


if __name__ == "__main__":
    main()
