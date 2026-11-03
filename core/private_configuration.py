"""Keep app-managed connection credentials outside model-readable tool output."""
from pathlib import Path
from core.path_grounding import build_path_context


def is_private_connection_path(path: str | Path) -> bool:
    target = Path(path).expanduser().resolve(strict=False)
    root = (build_path_context().user_data_root / 'connections').resolve(strict=False)
    return target == root or target.is_relative_to(root)


def redact_connection_secrets(text: str) -> str:
    from core.search_connections import SearchConnections
    from core.model_connections import ModelConnections
    secrets = set()
    try:
        secrets.add(SearchConnections().key())
    except (ValueError, OSError):
        pass
    try:
        secrets.update(item.get('api_key', '') for item in ModelConnections().connections().values())
    except (ValueError, OSError):
        pass
    for secret in sorted((key for key in secrets if isinstance(key, str) and len(key) >= 8), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    return text
