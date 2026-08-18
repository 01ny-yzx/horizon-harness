"""User-facing helpers for generated file outputs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DIRECTORY_ALIASES = {"desktop", "桌面", "downloads", "download", "下载", "documents", "document", "文档"}


def looks_like_directory_request(path: str) -> bool:
    stripped = str(path or "").strip().replace("\\", "/")
    return stripped.lower() in DIRECTORY_ALIASES or stripped.endswith("/")


def default_filename_for_content(content: str, output_format: str | None = None) -> str:
    fmt = str(output_format or "").strip().lower().lstrip(".")
    if fmt:
        return f"output.{_safe_extension(fmt)}"
    text = str(content or "").strip()
    if _looks_like_json(text):
        return "output.json"
    if _looks_like_csv(text):
        return "output.csv"
    if _looks_like_markdown(text):
        return "output.md"
    return "output.txt"


def safe_filename(name: str, fallback: str = "output.txt") -> str:
    value = Path(str(name or fallback)).name
    value = re.sub(r"[^A-Za-z0-9_. -]+", "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return value or fallback


def resolve_collision(path: Path) -> tuple[Path, bool, str]:
    if not path.exists():
        return path, False, ""
    parent = path.parent
    suffix = path.suffix
    stem = path.stem or "output"
    for index in range(1, 10_000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate, True, str(path)
    raise FileExistsError(f"No available output filename for {path}")


def open_directory_payload(path: str | None, target_type: str) -> dict[str, str]:
    if not path:
        return {"open_directory": "", "open_directory_label": "", "open_directory_hint": ""}
    directory = str(Path(path).parent)
    if target_type == "artifact":
        hint = "可下载文件已保存到 artifact 输出目录，客户端可用下载链接打开。"
        label = "artifact 输出目录"
    elif target_type == "local_path":
        hint = "客户端可根据该目录路径提供“打开输出目录”按钮。"
        label = "打开输出目录"
    else:
        hint = ""
        label = ""
    return {
        "open_directory": directory,
        "open_directory_label": label,
        "open_directory_hint": hint,
    }


def friendly_denied_payload(
    *,
    code: str,
    reason: str,
    requested_path: str,
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    suggestion = "请提供存在且有效的宿主机路径。"
    friendly = "无法使用这个输出路径。"
    if code == "agent_access_mode_read_only":
        friendly = "当前 Agent 权限模式为 read_only，不允许写入、修改或删除文件。"
        suggestion = "如需允许写入，请将 AGENT_ACCESS_MODE 设置为 full_access。"
    elif code == "agent_self_protected_path_blocked":
        friendly = "当前运行时不会修改自身实现区。"
        suggestion = "请改用用户项目或其他明确授权的路径。"
    elif code in {"permission_error", "os_permission_denied"}:
        friendly = "操作系统拒绝访问这个路径。"
        suggestion = "请检查宿主机账户权限或选择可访问路径。"
    return {
        "code": code or "path_not_allowed",
        "reason": reason or friendly,
        "message": friendly,
        "suggestion": suggestion,
        "allowed_roots": allowed_roots or [],
        "requested_path": requested_path,
    }


def _looks_like_json(text: str) -> bool:
    if not text:
        return False
    return (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))


def _looks_like_csv(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    first_commas = lines[0].count(",")
    return first_commas > 0 and lines[1].count(",") == first_commas


def _looks_like_markdown(text: str) -> bool:
    return text.startswith("#") or "\n#" in text or "- " in text or "```" in text


def _safe_extension(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", value)
    return cleaned[:12] or "txt"
