"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience dependency.
    load_dotenv = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

# Load project-level settings. Existing system environment variables are kept,
# so users can store API keys outside the project directory.
if load_dotenv is not None:
    load_dotenv(dotenv_path=ENV_FILE)
else:
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines() if ENV_FILE.exists() else []:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the Agent."""

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_timeout: float = 60.0
    llm_reasoning_mode: str = "auto"
    llm_extra_body: dict[str, Any] | None = None
    llm_capability_preset: str = ""
    llm_capabilities_json: dict[str, Any] | None = None
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    temperature: float = 0.2
    debug_mode: bool = False
    embedding_enabled: bool = False
    embedding_provider: str = "openai_compatible"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int | None = None
    vector_search_top_k: int = 5
    vector_similarity_threshold: float = 0.2
    default_user_id: str = "default_user"
    default_project_id: str = "default_project"
    workspace_root: str = "workspace_store"
    enable_workspace_isolation: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_cors_allow_all: bool = False
    api_debug: bool = False
    api_auth_enabled: bool = False
    api_keys: tuple[str, ...] = ("dev-local-key",)
    api_key_header: str = "X-API-Key"
    api_status_public: bool = False
    rate_limit_enabled: bool = True
    daily_chat_limit: int = 100
    daily_rag_limit: int = 200
    daily_web_search_limit: int = 50
    daily_embedding_limit: int = 500
    daily_document_load_limit: int = 50
    daily_sandbox_limit: int = 100
    daily_browser_limit: int = 50
    cache_enabled: bool = True
    cache_root: str = "cache_store"
    rag_cache_ttl_seconds: int = 1800
    embedding_cache_enabled: bool = True
    sandbox_enabled: bool = True
    sandbox_timeout_seconds: int = 30
    sandbox_max_output_chars: int = 12000
    tool_result_externalize_chars: int = 12000
    tool_result_preview_chars: int = 2000
    sandbox_cleanup_on_start: bool = False
    agent_access_mode: str = "read_only"
    agent_default_output_dir: str = "exports"
    browser_enabled: bool = True
    browser_headless: bool = True
    browser_timeout_ms: int = 15000
    browser_max_steps: int = 8
    browser_max_text_chars: int = 12000
    browser_screenshot_enabled: bool = True
    browser_screenshot_dir: str = "browser_artifacts"
    browser_allow_external: bool = True
    browser_block_private_ip: bool = True
    browser_channel: str = ""
    BROWSER_CHANNEL: str = ""
    browser_user_agent: str = ""
    mcp_enabled: bool = False
    mcp_config_path: str = "config/mcp_servers.local.json"
    mcp_config_dir: str = "config/mcp.d"
    mcp_default_timeout_seconds: float = 30.0
    mcp_max_result_chars: int = 12000
    mcp_gateway_enabled: bool = False
    cloud_mcp_gateway_enabled: bool = False
    cloud_mcp_gateway_id: str = "default-cloud-mcp-gateway"
    cloud_mcp_gateway_name: str = "Cloud MCP Gateway"
    cloud_mcp_gateway_base_path: str = "/cloud-mcp"
    cloud_mcp_catalog_enabled: bool = True
    cloud_mcp_catalog_include_preview: bool = True
    cloud_mcp_oauth_foundation_enabled: bool = True
    cloud_mcp_token_store_enabled: bool = True
    cloud_mcp_oauth_providers_enabled: bool = True
    cloud_mcp_permission_model_enabled: bool = True
    cloud_mcp_require_confirmation_for_write: bool = True
    cloud_mcp_require_confirmation_for_sensitive: bool = True
    cloud_mcp_remote_connection_enabled: bool = True
    cloud_mcp_remote_execution_enabled: bool = False
    cloud_mcp_remote_connection_mode: str = "foundation"
    context_budget_enabled: bool = True
    context_budget_auto_compact: bool = True
    context_budget_prune_tool_outputs: bool = True
    session_compaction_auto: bool = True
    session_compaction_buffer_tokens: int = 20_000
    session_compaction_keep_tokens: int = 8_000


def _get_bool_env(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""

    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    return default


def _get_optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_first_env(names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = _get_optional_env(name)
        if value:
            return value
    return default


def _is_placeholder_secret(value: str) -> bool:
    return value.strip() in {"your_deepseek_api_key_here", "your_llm_api_key_here"}


def _get_optional_int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_llm_reasoning_mode() -> str:
    value = os.getenv("LLM_REASONING_MODE", "").strip().lower()
    if value in {"auto", "enabled", "disabled"}:
        return value
    return "auto"


def _get_llm_extra_body() -> dict[str, Any] | None:
    raw = os.getenv("LLM_EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM_EXTRA_BODY must be valid JSON object text.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("LLM_EXTRA_BODY must parse to a JSON object.")
    return value


def _get_json_object_env(name: str) -> dict[str, Any] | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be valid JSON object text.") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must parse to a JSON object.")
    return value


def _get_csv_env(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _get_agent_access_mode() -> str:
    value = os.getenv("AGENT_ACCESS_MODE", "read_only").strip().lower()
    return value if value in {"read_only", "full_access"} else "read_only"


def _get_agent_default_output_dir() -> str:
    return os.getenv("AGENT_DEFAULT_OUTPUT_DIR", "exports").strip() or "exports"


def _get_cloud_mcp_gateway_base_path() -> str:
    value = os.getenv("CLOUD_MCP_GATEWAY_BASE_PATH", "/cloud-mcp").strip()
    if not value.startswith("/") or value == "/" or " " in value or "?" in value or "#" in value:
        return "/cloud-mcp"
    return value.rstrip("/") or "/cloud-mcp"


def _get_env_with_dotenv_fallback(name: str, default: str = "") -> str:
    """Read env, then fall back to a direct .env lookup when env is empty."""

    value = os.getenv(name, "").strip()
    if value:
        return value
    if not ENV_FILE.exists():
        return default
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() == name:
            return raw_value.strip().strip('"').strip("'")
    return default


DEBUG_MODE = _get_bool_env("AGENT_DEBUG", default=False)
LLM_PROVIDER = _get_optional_env("LLM_PROVIDER", "openai_compatible") or "openai_compatible"
LLM_API_KEY = _get_first_env(("LLM_API_KEY", "DEEPSEEK_API_KEY"), "")
if _is_placeholder_secret(LLM_API_KEY):
    LLM_API_KEY = ""
DEEPSEEK_API_KEY = _get_optional_env("DEEPSEEK_API_KEY")
if _is_placeholder_secret(DEEPSEEK_API_KEY):
    DEEPSEEK_API_KEY = ""
LLM_BASE_URL = _get_first_env(("LLM_BASE_URL", "DEEPSEEK_BASE_URL"), "https://api.deepseek.com")
LLM_MODEL = _get_first_env(("LLM_MODEL", "DEEPSEEK_MODEL"), "deepseek-chat")
LLM_TEMPERATURE = _get_float_env("LLM_TEMPERATURE", _get_float_env("AGENT_TEMPERATURE", 0.2))
LLM_TIMEOUT = _get_float_env("LLM_TIMEOUT", 60.0)
LLM_REASONING_MODE = _get_llm_reasoning_mode()
LLM_EXTRA_BODY = _get_llm_extra_body()
LLM_CAPABILITY_PRESET = _get_optional_env("LLM_CAPABILITY_PRESET")
LLM_CAPABILITIES_JSON = _get_json_object_env("LLM_CAPABILITIES_JSON")


settings = Settings(
    llm_provider=LLM_PROVIDER,
    llm_base_url=LLM_BASE_URL,
    llm_api_key=LLM_API_KEY,
    llm_model=LLM_MODEL,
    llm_temperature=LLM_TEMPERATURE,
    llm_timeout=LLM_TIMEOUT,
    llm_reasoning_mode=LLM_REASONING_MODE,
    llm_extra_body=LLM_EXTRA_BODY,
    llm_capability_preset=LLM_CAPABILITY_PRESET,
    llm_capabilities_json=LLM_CAPABILITIES_JSON,
    deepseek_api_key=DEEPSEEK_API_KEY,
    deepseek_base_url=_get_optional_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    deepseek_model=_get_optional_env("DEEPSEEK_MODEL", "deepseek-chat"),
    temperature=LLM_TEMPERATURE,
    debug_mode=DEBUG_MODE,
    embedding_enabled=_get_bool_env("EMBEDDING_ENABLED", default=False),
    embedding_provider=os.getenv("EMBEDDING_PROVIDER", "openai_compatible"),
    embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
    embedding_base_url=os.getenv("EMBEDDING_BASE_URL", ""),
    embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
    embedding_dim=_get_optional_int_env("EMBEDDING_DIM"),
    vector_search_top_k=_get_int_env("VECTOR_SEARCH_TOP_K", 5),
    vector_similarity_threshold=_get_float_env("VECTOR_SIMILARITY_THRESHOLD", 0.2),
    default_user_id=os.getenv("DEFAULT_USER_ID", "default_user"),
    default_project_id=os.getenv("DEFAULT_PROJECT_ID", "default_project"),
    workspace_root=os.getenv("WORKSPACE_ROOT", "workspace_store"),
    enable_workspace_isolation=_get_bool_env("ENABLE_WORKSPACE_ISOLATION", default=True),
    api_host=os.getenv("API_HOST", "127.0.0.1"),
    api_port=_get_int_env("API_PORT", 8000),
    api_cors_allow_all=_get_bool_env("API_CORS_ALLOW_ALL", default=False),
    api_debug=_get_bool_env("API_DEBUG", default=False),
    api_auth_enabled=_get_bool_env("API_AUTH_ENABLED", default=False),
    api_keys=_get_csv_env("API_KEYS", "dev-local-key"),
    api_key_header=os.getenv("API_KEY_HEADER", "X-API-Key"),
    api_status_public=_get_bool_env("API_STATUS_PUBLIC", default=False),
    rate_limit_enabled=_get_bool_env("RATE_LIMIT_ENABLED", default=True),
    daily_chat_limit=_get_int_env("DAILY_CHAT_LIMIT", 100),
    daily_rag_limit=_get_int_env("DAILY_RAG_LIMIT", 200),
    daily_web_search_limit=_get_int_env("DAILY_WEB_SEARCH_LIMIT", 50),
    daily_embedding_limit=_get_int_env("DAILY_EMBEDDING_LIMIT", 500),
    daily_document_load_limit=_get_int_env("DAILY_DOCUMENT_LOAD_LIMIT", 50),
    daily_sandbox_limit=_get_int_env("DAILY_SANDBOX_LIMIT", 100),
    daily_browser_limit=_get_int_env("DAILY_BROWSER_LIMIT", 50),
    cache_enabled=_get_bool_env("CACHE_ENABLED", default=True),
    cache_root=os.getenv("CACHE_ROOT", "cache_store"),
    rag_cache_ttl_seconds=_get_int_env("RAG_CACHE_TTL_SECONDS", 1800),
    embedding_cache_enabled=_get_bool_env("EMBEDDING_CACHE_ENABLED", default=True),
    sandbox_enabled=_get_bool_env("SANDBOX_ENABLED", default=True),
    sandbox_timeout_seconds=_get_int_env("SANDBOX_TIMEOUT_SECONDS", 30),
    sandbox_max_output_chars=_get_int_env("SANDBOX_MAX_OUTPUT_CHARS", 12000),
    tool_result_externalize_chars=_get_int_env("TOOL_RESULT_EXTERNALIZE_CHARS", 12000),
    tool_result_preview_chars=_get_int_env("TOOL_RESULT_PREVIEW_CHARS", 2000),
    sandbox_cleanup_on_start=_get_bool_env("SANDBOX_CLEANUP_ON_START", default=False),
    agent_access_mode=_get_agent_access_mode(),
    agent_default_output_dir=_get_agent_default_output_dir(),
    browser_enabled=_get_bool_env("BROWSER_ENABLED", default=True),
    browser_headless=_get_bool_env("BROWSER_HEADLESS", default=True),
    browser_timeout_ms=_get_int_env("BROWSER_TIMEOUT_MS", 15000),
    browser_max_steps=_get_int_env("BROWSER_MAX_STEPS", 8),
    browser_max_text_chars=_get_int_env("BROWSER_MAX_TEXT_CHARS", 12000),
    browser_screenshot_enabled=_get_bool_env("BROWSER_SCREENSHOT_ENABLED", default=True),
    browser_screenshot_dir=os.getenv("BROWSER_SCREENSHOT_DIR", "browser_artifacts"),
    browser_allow_external=_get_bool_env("BROWSER_ALLOW_EXTERNAL", default=True),
    browser_block_private_ip=_get_bool_env("BROWSER_BLOCK_PRIVATE_IP", default=True),
    browser_channel=_get_env_with_dotenv_fallback("BROWSER_CHANNEL", "").strip().lower(),
    BROWSER_CHANNEL=_get_env_with_dotenv_fallback("BROWSER_CHANNEL", "").strip().lower(),
    browser_user_agent=os.getenv("BROWSER_USER_AGENT", "").strip(),
    mcp_enabled=_get_bool_env("MCP_ENABLED", default=False),
    mcp_config_path=os.getenv("MCP_CONFIG_PATH", "config/mcp_servers.local.json").strip() or "config/mcp_servers.local.json",
    mcp_config_dir=os.getenv("MCP_CONFIG_DIR", "config/mcp.d").strip() or "config/mcp.d",
    mcp_default_timeout_seconds=_get_float_env("MCP_DEFAULT_TIMEOUT_SECONDS", 30.0),
    mcp_max_result_chars=_get_int_env("MCP_MAX_RESULT_CHARS", 12000),
    mcp_gateway_enabled=_get_bool_env("MCP_GATEWAY_ENABLED", default=False),
    cloud_mcp_gateway_enabled=_get_bool_env("CLOUD_MCP_GATEWAY_ENABLED", default=False),
    cloud_mcp_gateway_id=os.getenv("CLOUD_MCP_GATEWAY_ID", "default-cloud-mcp-gateway").strip() or "default-cloud-mcp-gateway",
    cloud_mcp_gateway_name=os.getenv("CLOUD_MCP_GATEWAY_NAME", "Cloud MCP Gateway").strip() or "Cloud MCP Gateway",
    cloud_mcp_gateway_base_path=_get_cloud_mcp_gateway_base_path(),
    cloud_mcp_catalog_enabled=_get_bool_env("CLOUD_MCP_CATALOG_ENABLED", default=True),
    cloud_mcp_catalog_include_preview=_get_bool_env("CLOUD_MCP_CATALOG_INCLUDE_PREVIEW", default=True),
    cloud_mcp_oauth_foundation_enabled=_get_bool_env("CLOUD_MCP_OAUTH_FOUNDATION_ENABLED", default=True),
    cloud_mcp_token_store_enabled=_get_bool_env("CLOUD_MCP_TOKEN_STORE_ENABLED", default=True),
    cloud_mcp_oauth_providers_enabled=_get_bool_env("CLOUD_MCP_OAUTH_PROVIDERS_ENABLED", default=True),
    cloud_mcp_permission_model_enabled=_get_bool_env("CLOUD_MCP_PERMISSION_MODEL_ENABLED", default=True),
    cloud_mcp_require_confirmation_for_write=_get_bool_env("CLOUD_MCP_REQUIRE_CONFIRMATION_FOR_WRITE", default=True),
    cloud_mcp_require_confirmation_for_sensitive=_get_bool_env("CLOUD_MCP_REQUIRE_CONFIRMATION_FOR_SENSITIVE", default=True),
    cloud_mcp_remote_connection_enabled=_get_bool_env("CLOUD_MCP_REMOTE_CONNECTION_ENABLED", default=True),
    cloud_mcp_remote_execution_enabled=_get_bool_env("CLOUD_MCP_REMOTE_EXECUTION_ENABLED", default=False),
    cloud_mcp_remote_connection_mode=os.getenv("CLOUD_MCP_REMOTE_CONNECTION_MODE", "foundation").strip() or "foundation",
    context_budget_enabled=_get_bool_env("CONTEXT_BUDGET_ENABLED", default=True),
    context_budget_auto_compact=_get_bool_env("CONTEXT_COMPACT_AUTO", default=True),
    context_budget_prune_tool_outputs=_get_bool_env("CONTEXT_PRUNE_TOOL_OUTPUTS", default=True),
    session_compaction_auto=_get_bool_env("SESSION_COMPACTION_AUTO", default=True),
    session_compaction_buffer_tokens=_get_int_env("SESSION_COMPACTION_BUFFER_TOKENS", 20_000),
    session_compaction_keep_tokens=_get_int_env("SESSION_COMPACTION_KEEP_TOKENS", 8_000),
)


def has_llm_provider_configured(runtime_settings: Settings = settings) -> bool:
    """Return whether runtime settings have enough model config for classifier use."""

    return bool(runtime_settings.llm_api_key.strip() and runtime_settings.llm_model.strip())
