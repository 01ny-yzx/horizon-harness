"""Offline model token-limit catalog generated from models.dev."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


CATALOG_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = CATALOG_DIR / "model_catalog_snapshot.json"
OVERRIDES_PATH = CATALOG_DIR / "model_catalog_overrides.json"
ALIASES_PATH = CATALOG_DIR / "model_aliases.json"
ENDPOINTS_PATH = CATALOG_DIR / "provider_endpoint_hosts.json"
VERTEX_REGION_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-aiplatform\.googleapis\.com$")


@dataclass(frozen=True)
class ModelLimitResolution:
    canonical_id: str
    max_context_tokens: int | None
    max_output_tokens: int | None
    max_input_tokens: int | None
    provider_id: str
    status: str
    source: str
    snapshot_sha256: str
    upstream_sha256: str
    candidate_provider_ids: tuple[str, ...] = ()
    candidate_model_ids: tuple[str, ...] = ()
    resolution_reason: str = "unresolved"
    ambiguous: bool = False


def resolve_model_limit(
    runtime_provider: str,
    runtime_model: str,
    *,
    base_url: str = "",
) -> ModelLimitResolution:
    snapshot = _load_json(SNAPSHOT_PATH, {})
    records = snapshot.get("models") if isinstance(snapshot, dict) else []
    records = records if isinstance(records, list) else []
    by_id = {str(item.get("canonical_id") or ""): item for item in records if isinstance(item, dict)}

    model = str(runtime_model or "").strip()
    provider_candidates = resolve_provider_candidates(base_url)

    scoped_ids = tuple(
        canonical
        for provider_id in provider_candidates
        if (canonical := f"{provider_id}/{model}") in by_id
    )
    if len(scoped_ids) == 1:
        return _record_resolution(snapshot, by_id[scoped_ids[0]], provider_candidates, scoped_ids, "endpoint_provider_exact_model", "catalog_host")
    if len(scoped_ids) > 1:
        scoped_records = [_apply_override(by_id[canonical], canonical) for canonical in scoped_ids]
        limit_keys = {_limit_key(record) for record in scoped_records}
        if len(limit_keys) == 1 and next(iter(limit_keys))[0] is not None and next(iter(limit_keys))[2] is not None:
            context, input_limit, output = next(iter(limit_keys))
            return ModelLimitResolution(
                canonical_id="",
                max_context_tokens=context,
                max_input_tokens=input_limit,
                max_output_tokens=output,
                provider_id="",
                status="shared",
                source="catalog_host",
                snapshot_sha256=_file_sha256(SNAPSHOT_PATH),
                upstream_sha256=str(snapshot.get("upstream_sha256") or ""),
                candidate_provider_ids=provider_candidates,
                candidate_model_ids=scoped_ids,
                resolution_reason="shared_identical_limits",
                ambiguous=False,
            )
        return _empty_resolution(snapshot, provider_candidates, scoped_ids, "ambiguous_provider_model", ambiguous=True)

    if model in by_id:
        record = by_id[model]
        canonical_provider = str(record.get("provider_id") or "")
        if provider_candidates and canonical_provider not in provider_candidates:
            return _empty_resolution(snapshot, provider_candidates, (model,), "ambiguous_provider_model", ambiguous=True)
        return _record_resolution(snapshot, record, provider_candidates or (canonical_provider,), (model,), "canonical_exact", "catalog_canonical")

    if not provider_candidates:
        global_matches = tuple(
            str(item.get("canonical_id") or "")
            for item in records
            if isinstance(item, dict) and item.get("model_id") == model
        )
        if len(global_matches) == 1:
            record = by_id[global_matches[0]]
            return _record_resolution(snapshot, record, (str(record.get("provider_id") or ""),), global_matches, "unique_global_model_id", "catalog_unique_model_id")

    alias = _exact_alias(runtime_provider, model, base_url)
    if alias:
        alias_record = by_id.get(alias)
        alias_provider = str(alias_record.get("provider_id") or "") if isinstance(alias_record, dict) else ""
        if isinstance(alias_record, dict) and (not provider_candidates or alias_provider in provider_candidates):
            return _record_resolution(snapshot, alias_record, provider_candidates or (alias_provider,), (alias,), "exact_alias", "catalog_alias")
    return _empty_resolution(snapshot, provider_candidates, (), "unresolved")


def snapshot_metadata() -> dict[str, Any]:
    snapshot = _load_json(SNAPSHOT_PATH, {})
    if not isinstance(snapshot, dict):
        return {}
    result = {key: snapshot.get(key) for key in ("schema_version", "model_count", "provider_count", "upstream_repository", "upstream_sha256")}
    result["snapshot_sha256"] = _file_sha256(SNAPSHOT_PATH)
    return result


def resolve_provider_candidates(base_url: str) -> tuple[str, ...]:
    """Return every exact Provider candidate for an endpoint host."""

    host = _normalize_host(base_url)
    if not host or is_non_identity_endpoint(host):
        return ()
    candidates: set[str] = set()
    endpoints = merged_provider_endpoints()
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        exact_hosts = {_normalize_host(value) for value in item.get("exact_hosts", []) if str(value or "")}
        if host in exact_hosts:
            candidates.add(str(item.get("provider_id") or ""))
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        for raw_suffix in item.get("controlled_suffixes", []) if isinstance(item.get("controlled_suffixes"), list) else []:
            suffix = _normalize_host(raw_suffix)
            if suffix and (host == suffix or host.endswith(f".{suffix}")):
                candidates.add(str(item.get("provider_id") or ""))
    if VERTEX_REGION_HOST_RE.fullmatch(host):
        candidates.add("google-vertex")
    return tuple(sorted(candidate for candidate in candidates if candidate))


def resolve_provider_id(base_url: str) -> str:
    """Compatibility wrapper that only resolves an unambiguous endpoint."""

    candidates = resolve_provider_candidates(base_url)
    return candidates[0] if len(candidates) == 1 else ""


def is_non_identity_endpoint(host: str) -> bool:
    """Return true for local bind addresses that do not prove Provider identity."""

    raw = str(host or "").strip().lower().rstrip(".")
    if raw in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or raw.endswith(".localhost"):
        return True
    normalized = _normalize_host(raw)
    return normalized in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or normalized.endswith(".localhost")


def merged_provider_endpoints() -> list[dict[str, Any]]:
    """Merge snapshot endpoints with verified local additions and corrections."""

    snapshot = _load_json(SNAPSHOT_PATH, {})
    providers = snapshot.get("providers") if isinstance(snapshot, dict) else []
    merged: dict[str, dict[str, Any]] = {}
    for item in providers if isinstance(providers, list) else []:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        if not provider_id:
            continue
        merged[provider_id] = {
            "provider_id": provider_id,
            "exact_hosts": _normalized_hosts(item.get("endpoint_hosts")),
            "controlled_suffixes": [],
            "sources": [str(item.get("endpoint_source") or "models.dev")],
        }

    local = _load_json(ENDPOINTS_PATH, [])
    for item in local if isinstance(local, list) else []:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        if not provider_id:
            continue
        target = merged.setdefault(
            provider_id,
            {"provider_id": provider_id, "exact_hosts": [], "controlled_suffixes": [], "sources": []},
        )
        removed_exact = set(_normalized_hosts(item.get("remove_exact_hosts")))
        removed_suffixes = set(_normalized_hosts(item.get("remove_controlled_suffixes")))
        exact = (set(target.get("exact_hosts") or []) - removed_exact) | set(_normalized_hosts(item.get("exact_hosts")))
        suffixes = (set(target.get("controlled_suffixes") or []) - removed_suffixes) | set(
            _normalized_hosts(item.get("controlled_suffixes"))
        )
        target["exact_hosts"] = sorted(exact)
        target["controlled_suffixes"] = sorted(suffixes)
        source_record = {
            key: str(item.get(key) or "")
            for key in ("source", "reason", "verified_at")
            if item.get(key)
        }
        if source_record:
            target.setdefault("local_verification", []).append(source_record)
        target.setdefault("sources", []).append(str(item.get("source") or "local_verified_catalog"))

    result: list[dict[str, Any]] = []
    for provider_id in sorted(merged):
        item = merged[provider_id]
        item["exact_hosts"] = sorted(set(_normalized_hosts(item.get("exact_hosts"))))
        item["controlled_suffixes"] = sorted(set(_normalized_hosts(item.get("controlled_suffixes"))))
        item["sources"] = sorted(set(str(value) for value in item.get("sources", []) if value))
        if item.get("local_verification"):
            item["local_verification"] = sorted(
                item["local_verification"], key=lambda value: (value.get("verified_at", ""), value.get("source", ""), value.get("reason", ""))
            )
        result.append(item)
    return result


def _exact_alias(runtime_provider: str, runtime_model: str, base_url: str = "") -> str:
    aliases = _load_json(ALIASES_PATH, [])
    host = _normalize_host(base_url)
    for item in aliases if isinstance(aliases, list) else []:
        if not isinstance(item, dict):
            continue
        alias_host = str(item.get("runtime_host") or "")
        if item.get("runtime_provider") == runtime_provider and item.get("runtime_model") == runtime_model and (not alias_host or alias_host == host):
            return str(item.get("canonical_id") or "")
    return ""


def _normalize_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    return str(parsed.hostname or "").lower().rstrip(".")


def _normalized_hosts(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({host for value in values if (host := _normalize_host(str(value or "")))})


def _record_resolution(
    snapshot: dict[str, Any],
    raw_record: dict[str, Any],
    provider_candidates: tuple[str, ...],
    model_candidates: tuple[str, ...],
    reason: str,
    source: str,
) -> ModelLimitResolution:
    canonical_id = str(raw_record.get("canonical_id") or "")
    record = _apply_override(raw_record, canonical_id)
    return ModelLimitResolution(
        canonical_id=canonical_id,
        max_context_tokens=_positive_int(record.get("max_context_tokens")),
        max_output_tokens=_positive_int(record.get("max_output_tokens")),
        max_input_tokens=_positive_int(record.get("max_input_tokens")),
        provider_id=str(record.get("provider_id") or ""),
        status=str(record.get("status") or "active"),
        source=source,
        snapshot_sha256=_file_sha256(SNAPSHOT_PATH),
        upstream_sha256=str(snapshot.get("upstream_sha256") or record.get("source_sha256") or ""),
        candidate_provider_ids=tuple(sorted(set(provider_candidates))),
        candidate_model_ids=tuple(sorted(set(model_candidates))),
        resolution_reason=reason,
        ambiguous=False,
    )


def _empty_resolution(
    snapshot: dict[str, Any],
    provider_candidates: tuple[str, ...],
    model_candidates: tuple[str, ...],
    reason: str,
    *,
    ambiguous: bool = False,
) -> ModelLimitResolution:
    return ModelLimitResolution(
        canonical_id="",
        max_context_tokens=None,
        max_output_tokens=None,
        max_input_tokens=None,
        provider_id="",
        status="ambiguous" if ambiguous else "unresolved",
        source="",
        snapshot_sha256=_file_sha256(SNAPSHOT_PATH),
        upstream_sha256=str(snapshot.get("upstream_sha256") or ""),
        candidate_provider_ids=tuple(sorted(set(provider_candidates))),
        candidate_model_ids=tuple(sorted(set(model_candidates))),
        resolution_reason=reason,
        ambiguous=ambiguous,
    )


def _limit_key(record: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    return (
        _positive_int(record.get("max_context_tokens")),
        _positive_int(record.get("max_input_tokens")),
        _positive_int(record.get("max_output_tokens")),
    )


def _apply_override(record: dict[str, Any], canonical_id: str) -> dict[str, Any]:
    result = dict(record)
    overrides = _load_json(OVERRIDES_PATH, [])
    for item in overrides if isinstance(overrides, list) else []:
        if isinstance(item, dict) and item.get("canonical_id") == canonical_id:
            for key in ("max_context_tokens", "max_input_tokens", "max_output_tokens", "status"):
                if key in item:
                    result[key] = item[key]
    return result


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


__all__ = [
    "ModelLimitResolution",
    "merged_provider_endpoints",
    "resolve_model_limit",
    "resolve_provider_candidates",
    "is_non_identity_endpoint",
    "resolve_provider_id",
    "snapshot_metadata",
]
