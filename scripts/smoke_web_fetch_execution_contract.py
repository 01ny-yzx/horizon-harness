"""Offline WebFetch execution-contract and hard-timeout smoke."""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from core.browser_policy import BrowserPolicy
import core.browser_policy as browser_policy_module
from core.execution_boundary import ToolArgumentSnapshot, _argument_snapshot_url_guard
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_trace_dict
from core.tool_outcome_resolution import resolve_tool_outcome
import tools.web_tools as web_tools


class FakeAsyncResponse:
    def __init__(
        self,
        body: bytes = b"ok",
        *,
        status_code: int = 200,
        content_type: str = "text/plain",
        content_length: str | None = None,
        extra_headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        chunk_delay: float = 0.0,
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if content_length is not None:
            self.headers["content-length"] = content_length
        self.headers.update(extra_headers or {})
        self.chunks = chunks
        self.chunk_delay = chunk_delay
        self.iterations = 0
        self.closed = False

    async def aiter_bytes(self):
        for chunk in self.chunks if self.chunks is not None else [self.body]:
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            self.iterations += 1
            yield chunk


class FakeStreamContext:
    def __init__(self, response: FakeAsyncResponse | None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.response

    async def __aexit__(self, *_args):
        if self.response is not None:
            self.response.closed = True
        return False


class FakeAsyncClient:
    def __init__(self, responses=None, error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True
        return False

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.error is not None:
            return FakeStreamContext(None, self.error)
        return FakeStreamContext(self.responses.pop(0))


def envelope() -> ToolCallEnvelope:
    arguments = {"url": "https://example.com"}
    return ToolCallEnvelope(
        call_id="fetch-1",
        provider_call_id="fetch-1",
        source=ToolCallSource.STRUCTURED,
        raw_name="fetch_url",
        tool_name="fetch_url",
        canonical_name="fetch_url",
        executable_name="fetch_url",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
    )


def run_fetch(url: str = "https://example.com", **kwargs):
    response = kwargs.pop("response", FakeAsyncResponse())
    fake = kwargs.pop("fake", FakeAsyncClient([response]))
    client_options: dict = {}

    def client_factory(**options):
        client_options.update(options)
        return fake

    with patch.object(web_tools.httpx, "AsyncClient", side_effect=client_factory):
        result = web_tools.fetch_url(url, **kwargs)
    return result, fake, response, client_options


def assert_failed(result: dict, code: str) -> None:
    assert result["success"] is False, result
    assert result["status"] == "failed", result
    assert result["error_code"] == code, result
    assert "policy_code" not in result, result


def test_arguments_transport_and_schema() -> None:
    invalid = [
        (("",), {}), ((123,), {}), (("ftp://example.com",), {}),
        (("file:///tmp/a",), {}), (("data:text/plain,x",), {}),
        (("https:///missing",), {}), (("https://example.com",), {"format": "xml"}),
    ]
    invalid.extend(
        (("https://example.com",), {"timeout": value})
        for value in (0, -1, 121, "30", True, math.nan, math.inf)
    )
    for args, kwargs in invalid:
        fake = FakeAsyncClient([FakeAsyncResponse()])
        with patch.object(web_tools.httpx, "AsyncClient", return_value=fake):
            result = web_tools.fetch_url(*args, **kwargs)
        assert_failed(result, "invalid_arguments")
        assert fake.calls == []

    for format in ("text", "markdown", "html"):
        result, fake, _, options = run_fetch(format=format)
        assert result["success"] is True
        assert options == {"timeout": None, "follow_redirects": True}
        assert fake.calls[0]["method"] == "GET"
        assert fake.calls[0]["headers"]["Accept"] == web_tools.WEB_FETCH_ACCEPT[format]
    for timeout in (1, 30, 120):
        result, _, _, _ = run_fetch(timeout=timeout)
        assert result["success"] is True

    schema = next(
        item["function"] for item in web_tools.WEB_TOOL_SCHEMAS
        if item["function"]["name"] == "fetch_url"
    )
    properties = schema["parameters"]["properties"]
    assert set(properties) == {"url", "format", "timeout"}
    assert properties["format"]["default"] == "markdown"
    assert properties["timeout"]["maximum"] == 120


def test_local_private_routing_and_browser_policy() -> None:
    for url in (
        "http://localhost:3000", "http://127.0.0.1:8000",
        "http://192.168.1.20", "http://10.0.0.5", "http://[::1]:8080",
    ):
        result, fake, _, _ = run_fetch(url)
        assert result["success"] is True, result
        assert fake.calls[0]["url"] == url

    private_url = "http://127.0.0.1:8000"
    snapshot = ToolArgumentSnapshot(
        tool_name="fetch_url", base_tool="fetch_url",
        raw_arguments_text=json.dumps({"url": private_url}),
        parsed_arguments={"url": private_url}, raw_argument_values={"url": private_url},
        normalized_arguments={"url": private_url}, sanitized_arguments={"url": private_url},
        task_profile_raw_values={"provided_urls": ["http://localhost:3000"]},
        security_relevant_values={"url": private_url},
    )
    rejecting_policy = SimpleNamespace(
        check_url=lambda _url: (_ for _ in ()).throw(AssertionError("BrowserPolicy called"))
    )
    assert _argument_snapshot_url_guard("fetch_url", snapshot, rejecting_policy) is None
    assert_failed(web_tools.fetch_url("file:///tmp/test.txt"), "invalid_arguments")
    with patch.object(
        browser_policy_module,
        "settings",
        replace(browser_policy_module.settings, browser_enabled=True, browser_allow_external=True),
    ):
        decision = BrowserPolicy().check_url("http://localhost:3000")
    assert decision["allowed"] is False and decision["code"] == "blocked_localhost"


def test_status_safe_failures_and_cloudflare() -> None:
    for status in (403, 404, 429, 500):
        result, fake, _, _ = run_fetch(response=FakeAsyncResponse(status_code=status))
        assert_failed(result, "http_error")
        assert result["data"]["http_status"] == status
        assert result["error"] == "Unable to fetch https://example.com"
        assert len(fake.calls) == 1

    secret = "SECRET-WEBFETCH"
    for error, code in (
        (httpx.ReadTimeout(f"Authorization=Bearer {secret}"), "request_timeout"),
        (httpx.ConnectError(f"proxy_password={secret}"), "connection_failed"),
        (httpx.RequestError(f"Authorization=Bearer {secret}"), "connection_failed"),
        (RuntimeError(f"Authorization=Bearer {secret}"), "response_processing_failed"),
    ):
        fake = FakeAsyncClient(error=error)
        result, _, _, _ = run_fetch(fake=fake)
        assert_failed(result, code)
        observation = normalize_tool_result(envelope(), result)
        assert secret not in json.dumps(result, ensure_ascii=False)
        assert secret not in json.dumps(observation_to_trace_dict(observation), ensure_ascii=False)

    challenge = FakeAsyncResponse(
        status_code=403, extra_headers={"cf-mitigated": "challenge"}
    )
    fake = FakeAsyncClient([challenge, FakeAsyncResponse(b"retried")])
    result, fake, _, _ = run_fetch(fake=fake)
    assert result["success"] is True
    assert len(fake.calls) == 2
    assert fake.calls[0]["headers"]["User-Agent"] == web_tools.WEB_FETCH_BROWSER_USER_AGENT
    assert fake.calls[1]["headers"]["User-Agent"] == "HorizonRuntime"
    assert challenge.closed is True


def test_size_content_type_conversion_and_observation() -> None:
    limit = web_tools.WEB_FETCH_MAX_RESPONSE_BYTES
    declared = FakeAsyncResponse(content_length=str(limit + 1))
    result, _, _, _ = run_fetch(response=declared)
    assert_failed(result, "response_too_large")
    assert declared.iterations == 0 and declared.closed is True

    exact = FakeAsyncResponse(chunks=[b"x" * limit])
    result, _, _, _ = run_fetch(response=exact, format="text")
    assert result["success"] is True
    oversized = FakeAsyncResponse(chunks=[b"x" * limit, b"y"])
    result, _, _, _ = run_fetch(response=oversized)
    assert_failed(result, "response_too_large")

    allowed = (
        "text/plain", "text/html", "text/markdown", "application/json",
        "application/problem+json", "application/xml", "application/rss+xml",
        "application/javascript", "application/x-javascript", "",
    )
    for content_type in allowed:
        result, _, _, _ = run_fetch(response=FakeAsyncResponse(content_type=content_type), format="html")
        assert result["success"] is True, content_type
    for content_type in (
        "image/png", "image/jpeg", "application/pdf", "application/zip", "application/octet-stream",
    ):
        result, _, _, _ = run_fetch(response=FakeAsyncResponse(content_type=content_type))
        assert_failed(result, "unsupported_content_type")

    html = (
        b"<h1>Hello</h1><script>bad()</script>"
        b"<p>world <strong>wide</strong></p><style>.bad {}</style>"
    )
    markdown, _, _, _ = run_fetch(response=FakeAsyncResponse(html, content_type="text/html"))
    assert "# Hello" in markdown["data"]["output"]
    assert "world **wide**" in markdown["data"]["output"]
    assert "bad()" not in markdown["data"]["output"]
    text, _, _, _ = run_fetch(response=FakeAsyncResponse(html, content_type="text/html"), format="text")
    assert "Hello" in text["data"]["output"] and "world wide" in text["data"]["output"]
    raw, _, _, _ = run_fetch(response=FakeAsyncResponse(html, content_type="text/html"), format="html")
    assert raw["data"]["output"] == html.decode()
    error_text, _, _, _ = run_fetch(response=FakeAsyncResponse(b"404 Not Found"), format="text")
    assert error_text["success"] is True

    observation = normalize_tool_result(envelope(), error_text)
    assert observation.success is True and observation.output_text == "404 Not Found"
    failure = web_tools._fetch_failure("https://example.com", "http_error", http_status=403)
    outcome = resolve_tool_outcome(
        task_state=SimpleNamespace(metadata={}, task_type="simple", tool_failures=[]),
        tool_name="fetch_url", arguments={"url": "https://example.com"}, observation=failure,
    )
    assert outcome.kind == "allow_continue" and outcome.failure_disposition == "ordinary_failure"
    assert not hasattr(outcome, "next_tool")


class LocalWebFetchHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cloudflare_request_count = 0

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/fast")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/fast":
            body = b"fast local stream"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        if self.path == "/cloudflare":
            type(self).cloudflare_request_count += 1
            if type(self).cloudflare_request_count == 1:
                self.send_response(403)
                self.send_header("cf-mitigated", "challenge")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        for _ in range(12):
            try:
                self.wfile.write(b"slow\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            time.sleep(0.10)
        self.close_connection = True


def test_real_socket_hard_timeout_retry_redirect_and_fast_stream() -> None:
    LocalWebFetchHandler.cloudflare_request_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalWebFetchHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    proxy_bypass = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    try:
        with patch.dict(os.environ, proxy_bypass):
            started = time.perf_counter()
            slow = web_tools.fetch_url(f"{base}/slow", format="text", timeout=0.15)
            slow_elapsed = time.perf_counter() - started
            assert_failed(slow, "request_timeout")
            assert slow_elapsed < 0.60, slow_elapsed

            started = time.perf_counter()
            cloudflare = web_tools.fetch_url(f"{base}/cloudflare", format="text", timeout=0.20)
            cloudflare_elapsed = time.perf_counter() - started
            assert_failed(cloudflare, "request_timeout")
            assert LocalWebFetchHandler.cloudflare_request_count == 2
            assert cloudflare_elapsed < 0.65, cloudflare_elapsed

            fast = web_tools.fetch_url(f"{base}/fast", format="text", timeout=1)
            assert fast["success"] is True
            assert fast["data"]["output"] == "fast local stream"
            redirected = web_tools.fetch_url(f"{base}/redirect", format="text", timeout=1)
            assert redirected["success"] is True
            assert redirected["data"]["output"] == "fast local stream"
            print(
                "WebFetch real socket elapsed: "
                f"slow={slow_elapsed:.3f}s cloudflare={cloudflare_elapsed:.3f}s"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main() -> None:
    test_arguments_transport_and_schema()
    test_local_private_routing_and_browser_policy()
    test_status_safe_failures_and_cloudflare()
    test_size_content_type_conversion_and_observation()
    test_real_socket_hard_timeout_retry_redirect_and_fast_stream()
    print("smoke_web_fetch_execution_contract ok")


if __name__ == "__main__":
    main()
