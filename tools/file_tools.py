"""File-related tools.

All tools return the same structured JSON-like dict shape:

Success: ``{"success": True, "data": ...}``
Failure: ``{"success": False, "error": ...}``
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core.document_readers import DEFAULT_MAX_CHARS, read_document as read_local_document
from core.file_access_policy import FileAccessDecision, FileAccessPolicy
from core.file_output_policy import resolve_existing_write_target, resolve_output_target
from core.file_output_ux import (
    default_filename_for_content,
    friendly_denied_payload,
    looks_like_directory_request,
    open_directory_payload,
    resolve_collision,
    safe_filename,
)
from core.path_zone_policy import evaluate_file_output_path_zone


def _denied_response(target: Any) -> dict[str, Any]:
    payload = friendly_denied_payload(
        code=target.code or "path_not_allowed",
        reason=target.reason,
        requested_path=target.requested_path,
        allowed_roots=[],
    )
    payload.update(
        {
            "operation": "write",
            "scope": _scope_for_output_error(payload["code"]),
            "resolved_path": None,
            "path_grounding": getattr(target, "path_grounding", None),
        }
    )
    return {
        "success": False,
        "error": payload["message"],
        "data": payload,
    }


def _access_denied_response(decision: FileAccessDecision) -> dict[str, Any]:
    data = decision.to_dict()
    data["message"] = _friendly_access_message(decision.code)
    return {"success": False, "error": data["message"], "data": data}


def _read_decision(path: str, raw_requested_path: str | None = None) -> FileAccessDecision:
    return FileAccessPolicy().evaluate(path, operation="read", raw_requested_path=raw_requested_path)


def _read_execution_metadata(
    decision: FileAccessDecision | None,
    *,
    fallback_path: str = "",
) -> dict[str, Any]:
    """Return a detached execution-audit payload for file reads."""

    metadata: dict[str, Any] = {}
    path_value = ""
    if decision is not None:
        path_value = str(decision.resolved_path or decision.requested_path or "").strip()
        if isinstance(decision.path_grounding, dict):
            metadata["path_grounding"] = dict(decision.path_grounding)
    if not path_value:
        path_value = str(fallback_path or "").strip()
    if path_value:
        metadata["path"] = path_value
    return metadata


def _read_file_failure_response(
    *,
    decision: FileAccessDecision | None,
    requested_path: str,
    error: str,
    error_code: str = "",
    recoverable: bool = False,
    recovery_reason: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep read-file failure facts separate from execution metadata."""

    failure_data = dict(data or {})
    failure_data.pop("path_grounding", None)
    failure_data.setdefault("requested_path", str(requested_path or ""))
    failure_data["error_code"] = str(error_code or "")
    failure_data["recoverable"] = bool(recoverable)
    failure_data["recovery_reason"] = str(recovery_reason or "")
    if decision is not None and decision.resolved_path:
        failure_data.setdefault("path", str(decision.resolved_path))
    return {
        "success": False,
        "error": error,
        "error_code": str(error_code or ""),
        "recoverable": bool(recoverable),
        "recovery_reason": str(recovery_reason or ""),
        "data": failure_data,
        "metadata": _read_execution_metadata(decision, fallback_path=requested_path),
    }


def _friendly_access_message(code: str) -> str:
    if code == "agent_access_mode_read_only":
        return "当前 Agent 权限模式为 read_only，不允许写入、修改或删除文件。"
    if code == "agent_self_protected_path_blocked":
        return "当前运行时不会修改自身实现区。"
    return "宿主机无法访问这个位置。"


def _scope_for_output_error(code: str) -> str:
    if code == "agent_access_mode_read_only":
        return "denied_agent_access"
    if code == "agent_self_protected_path_blocked":
        return "denied_agent_self_protection"
    return "denied_os_access"


def _content_metadata(target: Any, content: str) -> dict[str, Any]:
    safe_path = str(target.safe_path or "")
    suffix = Path(safe_path).suffix.lstrip(".").lower() if safe_path else ""
    return {
        "requested_path": target.requested_path,
        "filename": Path(safe_path).name if safe_path else "",
        "content_preview": content[:120],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        "output_format": suffix or "unknown",
    }


def _write_result(
    target: Any,
    bytes_written: int,
    content: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "path": target.safe_path,
        "target_type": target.target_type,
        "artifact_id": target.artifact_id,
        "download_url": target.download_url,
        "bytes": bytes_written,
        **_content_metadata(target, content),
        **open_directory_payload(target.safe_path, target.target_type),
        "collision_resolved": False,
        "original_path": "",
        "suggestion": _success_suggestion(target.target_type),
        "path_grounding": getattr(target, "path_grounding", None),
    }
    if extra:
        data.update(extra)
    return {"success": True, "data": data}


def _success_suggestion(target_type: str) -> str:
    if target_type == "artifact":
        return "可以通过 download_url 下载生成的文件。"
    return "可以使用 open_directory 打开输出目录。"


def _requested_filename(path: str, content: str, filename: str | None) -> str | None:
    if filename:
        return safe_filename(filename)
    if looks_like_directory_request(path):
        return default_filename_for_content(content)
    return None


def _agent_self_write_guard(
    path: str,
    *,
    raw_requested_path: str | None = None,
    original_requested_path: str | None = None,
    filename: str | None = None,
) -> dict[str, Any] | None:
    decision = evaluate_file_output_path_zone(
        requested_path=path,
        raw_requested_path=raw_requested_path or original_requested_path or path,
        filename=filename,
        allow_agent_internal=True,
        default_relative_to_output_root=False,
    )
    if decision.code != "agent_self_protected_path_blocked":
        return None
    return _denied_response(decision)


def list_files(path: str = ".", raw_requested_path: str | None = None) -> dict[str, Any]:
    """List files and directories under ``path``."""

    try:
        decision = _read_decision(path, raw_requested_path)
        if not decision.allowed or not decision.resolved_path:
            return _access_denied_response(decision)
        target = Path(decision.resolved_path)
        if not target.exists():
            return {"success": False, "error": f"路径不存在: {path}", "data": {"path_grounding": decision.path_grounding}}
        if not target.is_dir():
            return {"success": False, "error": f"不是目录: {path}", "data": {"path_grounding": decision.path_grounding}}

        items = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )

        return {"success": True, "data": items, "metadata": {"path_grounding": decision.path_grounding}}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def read_file(path: str, raw_requested_path: str | None = None) -> dict[str, Any]:
    """Read a UTF-8 text file."""

    decision: FileAccessDecision | None = None
    try:
        decision = _read_decision(path, raw_requested_path)
        if not decision.allowed or not decision.resolved_path:
            denied = _access_denied_response(decision)
            return _read_file_failure_response(
                decision=decision,
                requested_path=path,
                error=str(denied.get("error") or ""),
                data=dict(denied.get("data") or {}),
            )
        target = Path(decision.resolved_path)
        if not target.exists():
            return _read_file_failure_response(
                decision=decision,
                requested_path=path,
                error=f"文件不存在: {path}",
                error_code="file_not_found",
                data={"requested_path": path, "path": str(target)},
            )
        if not target.is_file():
            return _read_file_failure_response(
                decision=decision,
                requested_path=path,
                error=f"不是文件: {path}",
                error_code="path_is_not_file",
                data={"requested_path": path, "path": str(target)},
            )

        content = target.read_text(encoding="utf-8")
        return {
            "success": True,
            "data": content,
            "metadata": _read_execution_metadata(decision, fallback_path=str(target)),
        }
    except UnicodeDecodeError:
        return _read_file_failure_response(
            decision=decision,
            requested_path=path,
            error="文件不是 UTF-8 文本，无法读取。",
            error_code="tool_resource_incompatible",
            recoverable=True,
            recovery_reason="selected_text_reader_cannot_decode_resource",
            data={
                "requested_path": path,
                "path": str(decision.resolved_path) if decision is not None and decision.resolved_path else "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _read_file_failure_response(
            decision=decision,
            requested_path=path,
            error=str(exc),
            data={
                "requested_path": path,
                "path": str(decision.resolved_path) if decision is not None and decision.resolved_path else "",
            },
        )


def read_document(
    path: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_tables: bool = True,
    raw_requested_path: str | None = None,
) -> dict[str, Any]:
    """Read common local documents through Document Reader v2."""

    decision = _read_decision(path, raw_requested_path)
    if not decision.allowed or not decision.resolved_path:
        response = _access_denied_response(decision)
        response["data"]["error"] = {"code": decision.code, "message": response["error"]}
        return response

    result = read_local_document(decision.resolved_path, max_chars=max_chars, include_tables=include_tables)
    payload = result.to_dict()
    execution_metadata = {
        "path": str(decision.resolved_path),
        "path_grounding": decision.path_grounding,
    }
    if result.success:
        return {"success": True, "data": payload, "metadata": execution_metadata}
    error = result.error or {"code": "document_read_failed", "message": "读取文档失败。"}
    return {
        "success": False,
        "error": error.get("message", "读取文档失败。"),
        "error_code": str(error.get("code") or "document_read_failed"),
        "data": payload,
        "metadata": execution_metadata,
    }


def write_file(
    path: str,
    content: str,
    filename: str | None = None,
    overwrite: bool = False,
    raw_requested_path: str | None = None,
    original_requested_path: str | None = None,
    allow_agent_internal: bool = False,
) -> dict[str, Any]:
    """Write UTF-8 text to ``path``, creating parent directories if needed."""

    try:
        self_guard = _agent_self_write_guard(
            path,
            raw_requested_path=raw_requested_path,
            original_requested_path=original_requested_path,
            filename=_requested_filename(path, content, filename),
        )
        if self_guard is not None:
            return self_guard
        target_info = resolve_output_target(
            path,
            filename=_requested_filename(path, content, filename),
            raw_requested_path=raw_requested_path or original_requested_path,
            allow_agent_internal=allow_agent_internal,
        )
        if target_info.target_type == "denied" or not target_info.safe_path:
            return _denied_response(target_info)
        target = Path(target_info.safe_path)
        original_path = str(target)
        collision_resolved = False
        if target_info.target_type == "local_path" and not overwrite:
            target, collision_resolved, original_path = resolve_collision(target)
            if collision_resolved:
                target_info.safe_path = str(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return _write_result(
            target_info,
            len(content.encode("utf-8")),
            content,
            {
                "collision_resolved": collision_resolved,
                "original_path": original_path if collision_resolved else "",
                "open_directory": str(target.parent),
                "filename": target.name,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def replace_in_file(
    path: str,
    old_text: str,
    new_text: str,
    raw_requested_path: str | None = None,
    original_requested_path: str | None = None,
    allow_agent_internal: bool = False,
) -> dict[str, Any]:
    """Replace exactly one occurrence of ``old_text`` in a UTF-8 file."""

    try:
        if not old_text:
            return {"success": False, "error": "old_text 不能为空。"}

        self_guard = _agent_self_write_guard(path, raw_requested_path=raw_requested_path, original_requested_path=original_requested_path)
        if self_guard is not None:
            return self_guard
        target_info = resolve_existing_write_target(
            path,
            raw_requested_path=raw_requested_path or original_requested_path,
            allow_agent_internal=allow_agent_internal,
        )
        if target_info.target_type == "denied" or not target_info.safe_path:
            return _denied_response(target_info)
        target = Path(target_info.safe_path)
        if not target.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        if not target.is_file():
            return {"success": False, "error": f"不是文件: {path}"}

        content = target.read_text(encoding="utf-8")
        replaced_count = content.count(old_text)
        if replaced_count == 0:
            return {
                "success": False,
                "error": "old_text 在文件中不存在，未执行写入。请先 read_file 确认精确文本。",
            }
        if replaced_count > 1:
            return {
                "success": False,
                "error": f"old_text 匹配到 {replaced_count} 处，未执行写入。请提供更精确的 old_text。",
            }

        updated = content.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        preview_start = max(0, updated.find(new_text) - 160)
        preview_end = min(len(updated), updated.find(new_text) + len(new_text) + 160)

        return _write_result(
            target_info,
            len(updated.encode("utf-8")),
            updated,
            {"replaced_count": replaced_count, "preview": updated[preview_start:preview_end]},
        )
    except UnicodeDecodeError:
        return {"success": False, "error": "文件不是 UTF-8 文本，无法修改。"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


FILE_TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "read_document": read_document,
    "write_file": write_file,
    "replace_in_file": replace_in_file,
}


FILE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出指定目录中的文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要查看的目录路径，默认为当前目录。",
                        "default": ".",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a local UTF-8 plain-text file, source file, or configuration file and return its raw text. It accepts a local filesystem path only and does not parse document sheets, tables, pages, paragraphs, or slides. The path must not be a URL or URI; use fetch_url for HTTP(S) network resources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Local filesystem path to read. URLs and URIs are not accepted.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Temporarily parse a local PDF, DOCX, XLSX, TXT, Markdown, JSON, or CSV for the current task without importing it into the knowledge base. Legacy DOC/XLS are rejected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的本地文档路径。",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "最多返回的正文字符数，默认 12000，超出会截断。",
                        "default": DEFAULT_MAX_CHARS,
                    },
                    "include_tables": {
                        "type": "boolean",
                        "description": "是否返回 CSV、DOCX、XLSX 中提取到的表格数据，默认 true。",
                        "default": True,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入 UTF-8 文本文件；优先用于创建新文件，不建议用于小范围修改已有文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径。",
                    },
                    "filename": {
                        "type": "string",
                        "description": "当 path 是目录时使用的文件名；省略时会按内容自动生成。",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的完整文本内容。",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "是否允许覆盖已有文件，默认 false；false 时会自动改名。",
                        "default": False,
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "在已有 UTF-8 文件中替换唯一匹配的 old_text。小范围代码修改优先使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要修改的文件路径。",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "文件中必须唯一匹配的原始文本。",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本。",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
]
