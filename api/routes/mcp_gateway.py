"""MCP Gateway JSON-RPC route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from api.deps import safe_error
from api.schemas import ApiResponse
from core.mcp_gateway import gateway_status, handle_jsonrpc_request
from core.mcp_runtime_manager import MCPRuntimeManager, get_mcp_runtime_manager


router = APIRouter(prefix="/mcp/gateway", tags=["mcp-gateway"])


def get_runtime_manager() -> MCPRuntimeManager:
    return get_mcp_runtime_manager()


@router.post("")
def post_gateway(payload: Any = Body(...)) -> JSONResponse:
    return _handle_gateway_payload(payload)


@router.post("/")
def post_gateway_slash(payload: Any = Body(...)) -> JSONResponse:
    return _handle_gateway_payload(payload)


@router.get("/status", response_model=ApiResponse)
def get_gateway_status() -> ApiResponse:
    try:
        return ApiResponse(success=True, data=gateway_status(get_runtime_manager()))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error={"code": "mcp_gateway_status_failed", "message": safe_error(exc)})


def _handle_gateway_payload(payload: Any) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid JSON-RPC request."}})
    response = handle_jsonrpc_request(payload, get_runtime_manager())
    return JSONResponse(response)
