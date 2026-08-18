"""Health and service status routes."""

from __future__ import annotations

import os

from fastapi import APIRouter

from api.schemas import ApiResponse
from config.settings import settings
from core.embedding_provider import EmbeddingProvider
from core.mcp_config import load_mcp_server_configs_from_sources
from providers.capabilities import reasoning_auto_policy
from providers.factory import config_from_settings
from providers.registry import resolve_provider_name


router = APIRouter(tags=["health"])


@router.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    """Return basic service health."""

    return ApiResponse(success=True, data=build_health_payload())


def build_health_payload() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "agent-api",
        "version": "0.1.0",
        "api": {
            "host": settings.api_host,
            "port": settings.api_port,
            "debug": settings.api_debug,
            "auth_enabled": settings.api_auth_enabled,
        },
        "features": {
            "embedding_enabled": settings.embedding_enabled,
            "workspace_isolation_enabled": settings.enable_workspace_isolation,
            "mcp_enabled": settings.mcp_enabled,
            "cache_enabled": settings.cache_enabled,
            "sandbox_enabled": settings.sandbox_enabled,
            "browser_enabled": settings.browser_enabled,
        },
    }


@router.get("/status", response_model=ApiResponse)
def status() -> ApiResponse:
    """Return safe backend configuration status without API keys."""

    embedding = EmbeddingProvider().get_status().get("data", {})
    legacy_deepseek_compat_enabled = any(
        os.getenv(name, "").strip()
        for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
    )
    llm_config = config_from_settings(settings)
    capabilities = llm_config.capabilities
    auto_policy = reasoning_auto_policy(settings.llm_reasoning_mode, capabilities) if capabilities else "normal_chat"
    llm_status = {
        "provider": resolve_provider_name(settings.llm_provider),
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "api_key_configured": bool(settings.llm_api_key),
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout,
        "reasoning_mode": settings.llm_reasoning_mode,
        "extra_body_configured": bool(settings.llm_extra_body),
        "reasoning": {
            "mode": settings.llm_reasoning_mode,
            "auto_policy": auto_policy,
            "extra_body_configured": bool(settings.llm_extra_body),
        },
        "capability_preset": llm_config.capability_preset_effective,
        "capability_preset_requested": llm_config.capability_preset_requested or "",
        "capability_preset_effective": llm_config.capability_preset_effective or "",
        "capability_preset_source": llm_config.capability_preset_source,
        "capabilities": capabilities.safe_dict() if capabilities else {},
        "legacy_deepseek_compat_enabled": legacy_deepseek_compat_enabled,
    }
    return ApiResponse(
        success=True,
        data={
            "llm": llm_status,
            "deepseek_configured": bool(settings.llm_api_key or settings.deepseek_api_key),
            "deepseek_model": settings.deepseek_model,
            "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
            "embedding": embedding,
            "workspace_isolation_enabled": settings.enable_workspace_isolation,
            "mcp": _safe_mcp_status(),
        },
    )


def _safe_mcp_status() -> dict:
    config_path = settings.mcp_config_path
    config_dir = getattr(settings, "mcp_config_dir", "config/mcp.d")
    data = {
        "enabled": settings.mcp_enabled,
        "config_path": config_path,
        "config_dir": config_dir,
        "servers_total": 0,
        "servers_enabled": 0,
        "tools_total": 0,
        "tools_enabled": 0,
        "tools_disabled": 0,
        "config_sources_total": 0,
        "config_sources_loaded": 0,
        "errors": [],
    }
    if not settings.mcp_enabled:
        return data
    result = load_mcp_server_configs_from_sources(config_path, config_dir)
    configs = result.configs
    data["servers_total"] = len(configs)
    data["servers_enabled"] = len([config for config in configs if config.enabled])
    data["config_sources_total"] = len(result.sources)
    data["config_sources_loaded"] = len([source for source in result.sources if source.loaded])
    data["errors"] = [{"code": str(error.get("code", "")), "path": str(error.get("path", ""))} for error in result.errors]
    if not configs:
        data["errors"].append({"code": "mcp_no_valid_servers"})
    return data
