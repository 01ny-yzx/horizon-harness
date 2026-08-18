"""Dynamic local port allocation helpers for the desktop client."""

from __future__ import annotations

import socket
from dataclasses import asdict, dataclass, field
from typing import Callable


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PREFERRED_PORT = 8000
DEFAULT_PORT_RANGE_START = 8000
DEFAULT_PORT_RANGE_END = 8999
MIN_PORT = 1
MAX_PORT = 65535


@dataclass(frozen=True)
class PortAllocationRequest:
    host: str = DEFAULT_HOST
    requested_port: int | None = None
    preferred_port: int = DEFAULT_PREFERRED_PORT
    range_start: int = DEFAULT_PORT_RANGE_START
    range_end: int = DEFAULT_PORT_RANGE_END


@dataclass(frozen=True)
class PortAllocationResult:
    ok: bool
    host: str
    selected_port: int | None
    requested_port: int | None
    preferred_port: int
    range_start: int
    range_end: int
    reason: str
    warnings: list[str] = field(default_factory=list)


def normalize_port(value: object, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return port if MIN_PORT <= port <= MAX_PORT else default


def is_valid_port(value: object) -> bool:
    return normalize_port(value) is not None


def is_port_available(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.bind((host, port))
            return True
    except OSError:
        return False


def allocate_port(
    request: PortAllocationRequest,
    *,
    port_checker: Callable[[str, int], bool] | None = None,
) -> PortAllocationResult:
    checker = port_checker or is_port_available
    warnings: list[str] = []
    requested_port = request.requested_port

    if requested_port is not None:
        normalized_requested = normalize_port(requested_port)
        if normalized_requested is None:
            return _result(request, None, "invalid_requested_port", False, warnings)
        if checker(request.host, normalized_requested):
            return _result(request, normalized_requested, "requested_port_available", True, warnings)
        return _result(request, None, "requested_port_unavailable", False, warnings)

    range_start = normalize_port(request.range_start)
    range_end = normalize_port(request.range_end)
    if range_start is None or range_end is None or range_start > range_end:
        return _result(request, None, "invalid_port_range", False, warnings)

    preferred_port = normalize_port(request.preferred_port)
    if preferred_port is None:
        preferred_port = DEFAULT_PREFERRED_PORT
        warnings.append("invalid_preferred_port")

    normalized_request = PortAllocationRequest(
        host=request.host,
        requested_port=None,
        preferred_port=preferred_port,
        range_start=range_start,
        range_end=range_end,
    )
    if checker(request.host, preferred_port):
        return _result(normalized_request, preferred_port, "preferred_port_available", True, warnings)

    warnings.append("preferred_port_unavailable")
    for candidate in range(range_start, range_end + 1):
        if candidate == preferred_port:
            continue
        if checker(request.host, candidate):
            return _result(normalized_request, candidate, "fallback_port_selected", True, warnings)

    return _result(normalized_request, None, "no_available_port", False, warnings)


def port_allocation_to_dict(result: PortAllocationResult) -> dict[str, object]:
    return asdict(result)


def _result(
    request: PortAllocationRequest,
    selected_port: int | None,
    reason: str,
    ok: bool,
    warnings: list[str],
) -> PortAllocationResult:
    return PortAllocationResult(
        ok=ok,
        host=request.host,
        selected_port=selected_port,
        requested_port=request.requested_port,
        preferred_port=request.preferred_port,
        range_start=request.range_start,
        range_end=request.range_end,
        reason=reason,
        warnings=list(warnings),
    )
