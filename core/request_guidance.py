"""Resolve authoritative guidance for one model request."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from core.instruction_context import load_initial_instruction_context
from core.persistent_memory import PersistentMemory
from core.unicode_safety import sanitize_unicode


REQUEST_GUIDANCE_TRANSITION_TEXT = (
    "The request guidance for this turn has changed from earlier turns.\n"
    "Treat the current request guidance as authoritative.\n"
    "Do not infer active instructions from previous assistant responses."
)


@dataclass(frozen=True)
class RequestGuidance:
    """Current system guidance and its request-local provenance."""

    system_messages: tuple[dict[str, Any], ...]
    instruction_paths: tuple[str, ...]
    unavailable_instruction_paths: tuple[str, ...]
    root_instruction_included: bool
    root_instruction_fingerprint: str
    root_instruction_chars: int
    persistent_guidance_included: bool
    persistent_guidance_fingerprint: str
    persistent_guidance_chars: int
    persistent_load_success: bool
    effective_guidance_fingerprint: str


@dataclass(frozen=True)
class RequestGuidanceTransition:
    """One-request auxiliary context for a changed guidance authority."""

    system_messages: tuple[dict[str, Any], ...]
    guidance_changed_since_previous_request: bool
    transition_note_included: bool


def resolve_request_guidance(
    *,
    project_root: str | Path,
    persistent_memory: PersistentMemory,
) -> RequestGuidance:
    """Freshly resolve root and scoped persistent guidance for one request."""

    root = load_initial_instruction_context(Path(project_root))
    messages: list[dict[str, Any]] = []
    if root.content:
        messages.append(
            {
                "role": "system",
                "content": root.content,
                "metadata": {
                    "note_type": "request_guidance",
                    "guidance_kind": "root_instruction",
                    "instruction_paths": list(root.paths),
                },
            }
        )

    loaded = persistent_memory.load_all()
    persistent_text = ""
    if loaded.get("success"):
        persistent_text = persistent_memory.format_instruction_context(max_chars=None)
    if persistent_text:
        messages.append(
            {
                "role": "system",
                "content": persistent_text,
                "metadata": {
                    "note_type": "request_guidance",
                    "guidance_kind": "persistent_instruction",
                },
            }
        )

    clean_messages = sanitize_unicode(messages)
    root_text = _guidance_text(clean_messages, "root_instruction")
    persistent_guidance_text = _guidance_text(
        clean_messages,
        "persistent_instruction",
    )
    return RequestGuidance(
        system_messages=tuple(dict(message) for message in clean_messages),
        instruction_paths=tuple(root.paths),
        unavailable_instruction_paths=tuple(root.unavailable_paths),
        root_instruction_included=bool(root_text),
        root_instruction_fingerprint=_text_fingerprint(root_text),
        root_instruction_chars=len(root_text),
        persistent_guidance_included=bool(persistent_guidance_text),
        persistent_guidance_fingerprint=_text_fingerprint(
            persistent_guidance_text
        ),
        persistent_guidance_chars=len(persistent_guidance_text),
        persistent_load_success=bool(loaded.get("success")),
        effective_guidance_fingerprint=_effective_guidance_fingerprint(
            clean_messages
        ),
    )


def resolve_request_guidance_transition(
    guidance: RequestGuidance,
    *,
    previous_effective_fingerprint: str,
) -> RequestGuidanceTransition:
    """Return a transient system note only when sent guidance has changed."""

    previous = str(previous_effective_fingerprint or "")
    changed = bool(
        previous
        and guidance.effective_guidance_fingerprint != previous
    )
    messages: tuple[dict[str, Any], ...] = ()
    if changed:
        messages = (
            {
                "role": "system",
                "content": REQUEST_GUIDANCE_TRANSITION_TEXT,
                "metadata": {
                    "note_type": "request_guidance_transition",
                },
            },
        )
    return RequestGuidanceTransition(
        system_messages=messages,
        guidance_changed_since_previous_request=changed,
        transition_note_included=bool(messages),
    )


def request_guidance_trace_payload(
    guidance: RequestGuidance,
    *,
    stage: str,
    resolution_mode: str = "fresh",
    guidance_changed_since_previous_request: bool = False,
    transition_note_included: bool = False,
) -> dict[str, Any]:
    """Return content-free evidence for one request's resolved guidance."""

    return {
        "stage": str(stage or ""),
        "resolution_mode": str(resolution_mode or "fresh"),
        "root_instruction_included": guidance.root_instruction_included,
        "instruction_paths": list(guidance.instruction_paths),
        "unavailable_instruction_paths": list(
            guidance.unavailable_instruction_paths
        ),
        "root_instruction_fingerprint": guidance.root_instruction_fingerprint,
        "root_instruction_chars": guidance.root_instruction_chars,
        "persistent_guidance_included": guidance.persistent_guidance_included,
        "persistent_guidance_fingerprint": (
            guidance.persistent_guidance_fingerprint
        ),
        "persistent_guidance_chars": guidance.persistent_guidance_chars,
        "persistent_load_success": guidance.persistent_load_success,
        "effective_guidance_fingerprint": (
            guidance.effective_guidance_fingerprint
        ),
        "guidance_changed_since_previous_request": bool(
            guidance_changed_since_previous_request
        ),
        "transition_note_included": bool(transition_note_included),
    }


def _guidance_text(
    messages: list[dict[str, Any]],
    guidance_kind: str,
) -> str:
    for message in messages:
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("guidance_kind") != guidance_kind:
            continue
        content = message.get("content")
        return content if isinstance(content, str) else ""
    return ""


def _text_fingerprint(content: str) -> str:
    if not content:
        return ""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _effective_guidance_fingerprint(
    messages: list[dict[str, Any]],
) -> str:
    canonical = [
        {
            "role": str(message.get("role") or ""),
            "content": str(message.get("content") or ""),
        }
        for message in messages
    ]
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
