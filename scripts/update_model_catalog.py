"""Generate Horizon's offline token-limit catalog from models.dev data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_URL = "https://models.dev/api.json"
DEFAULT_OUTPUT = ROOT / "providers" / "model_catalog_snapshot.json"
ENDPOINTS_PATH = ROOT / "providers" / "provider_endpoint_hosts.json"


def build_snapshot(raw: bytes, *, source: str) -> dict[str, Any]:
    payload = json.loads(raw)
    upstream_sha256 = hashlib.sha256(raw).hexdigest()
    records: list[dict[str, Any]] = []
    providers: list[dict[str, Any]] = []
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        _append_flat_models(records, payload["data"], source)
    elif isinstance(payload, dict):
        _append_provider_models(records, payload, source)
        providers = _provider_records(payload)
    records.sort(key=lambda item: (item["provider_id"], item["model_id"]))
    for record in records:
        record["source_sha256"] = upstream_sha256
    return {
        "schema_version": "horizon_model_catalog_v1",
        "upstream_repository": "https://github.com/anomalyco/models.dev",
        "upstream_sha256": upstream_sha256,
        "model_count": len(records),
        "provider_count": len({item["provider_id"] for item in records}),
        "providers": providers,
        "models": records,
    }


def _provider_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint_catalog = json.loads(ENDPOINTS_PATH.read_text(encoding="utf-8")) if ENDPOINTS_PATH.exists() else []
    endpoint_by_provider = {
        str(item.get("provider_id") or ""): item
        for item in endpoint_catalog
        if isinstance(item, dict) and item.get("provider_id")
    }
    records: list[dict[str, Any]] = []
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        hosts: list[str] = []
        api = str(provider.get("api") or "")
        host = _url_host(api)
        if host:
            hosts.append(host)
        endpoint_entry = endpoint_by_provider.get(str(provider_id), {})
        for value in endpoint_entry.get("exact_hosts", []) if isinstance(endpoint_entry.get("exact_hosts"), list) else []:
            if value not in hosts:
                hosts.append(value)
        records.append({
            "provider_id": str(provider_id),
            "provider_name": str(provider.get("name") or provider_id),
            "endpoint_hosts": hosts,
            "endpoint_source": "models.dev" if host else ("provider_endpoint_catalog" if hosts else ""),
        })
    return sorted(records, key=lambda item: item["provider_id"])


def _url_host(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    return str(parsed.hostname or "").lower().rstrip(".")


def _append_provider_models(records: list[dict[str, Any]], payload: dict[str, Any], source: str) -> None:
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models") if isinstance(provider.get("models"), dict) else {}
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            limit = model.get("limit") if isinstance(model.get("limit"), dict) else {}
            context = _positive_int(limit.get("context"))
            output = _positive_int(limit.get("output"))
            if context is None or output is None:
                continue
            records.append({
                "provider_id": str(provider_id),
                "model_id": str(model_id),
                "canonical_id": f"{provider_id}/{model_id}",
                "model_name": str(model.get("name") or model_id),
                "status": str(model.get("status") or "active"),
                "max_context_tokens": context,
                "max_input_tokens": _positive_int(limit.get("input")),
                "max_output_tokens": output,
                "release_date": model.get("release_date"),
                "last_updated": model.get("last_updated"),
                "source": source,
                "source_sha256": "",
            })


def _append_flat_models(records: list[dict[str, Any]], models: list[Any], source: str) -> None:
    for model in models:
        if not isinstance(model, dict):
            continue
        canonical = str(model.get("id") or "")
        provider_id, separator, model_id = canonical.partition("/")
        top = model.get("top_provider") if isinstance(model.get("top_provider"), dict) else {}
        context = _positive_int(model.get("context_length") or top.get("context_length"))
        output = _positive_int(top.get("max_completion_tokens"))
        if not separator or context is None or output is None:
            continue
        records.append({
            "provider_id": provider_id,
            "model_id": model_id,
            "canonical_id": canonical,
            "model_name": str(model.get("name") or model_id),
            "status": "active",
            "max_context_tokens": context,
            "max_input_tokens": None,
            "max_output_tokens": output,
            "release_date": None,
            "last_updated": None,
            "source": source,
            "source_sha256": "",
        })


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _read_source(source_url: str, source_file: str) -> tuple[bytes, str]:
    if source_file:
        path = Path(source_file)
        return path.read_bytes(), str(path)
    request = Request(source_url, headers={"User-Agent": "Horizon-Model-Catalog-Updater/1.0"})
    with urlopen(request, timeout=60) as response:
        return response.read(), source_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--source-file", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    raw, source = _read_source(args.source_url, args.source_file)
    snapshot = build_snapshot(raw, source=source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {snapshot['model_count']} models across {snapshot['provider_count']} providers to {output}")


if __name__ == "__main__":
    main()
