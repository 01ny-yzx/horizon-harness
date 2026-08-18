"""Project inspection and search tools for coding tasks."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any


IGNORED_NAMES = {".venv", ".idea", "__pycache__", ".git"}
MAX_TREE_ITEMS = 200
MAX_SEARCH_RESULTS = 50
MAX_FILE_BYTES = 1_000_000


def get_project_tree(path: str = ".", max_depth: int = 3) -> dict[str, Any]:
    """Return a compact directory tree while skipping noisy folders."""

    try:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            return {"success": False, "error": f"路径不存在: {path}"}
        if not root.is_dir():
            return {"success": False, "error": f"不是目录: {path}"}

        safe_depth = max(0, min(int(max_depth), 8))
        lines: list[str] = [f"{root.name}/"]
        count = 0
        truncated = False

        def walk(directory: Path, depth: int, prefix: str = "") -> None:
            nonlocal count, truncated
            if depth >= safe_depth or truncated:
                return

            children = [
                child
                for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                if child.name not in IGNORED_NAMES
            ]
            for child in children:
                if count >= MAX_TREE_ITEMS:
                    truncated = True
                    return
                count += 1
                marker = "/" if child.is_dir() else ""
                lines.append(f"{prefix}- {child.name}{marker}")
                if child.is_dir():
                    walk(child, depth + 1, prefix + "  ")

        walk(root, 0)
        return {
            "success": True,
            "data": {
                "root": str(root),
                "max_depth": safe_depth,
                "tree": "\n".join(lines),
                "truncated": truncated,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def find_files(pattern: str, path: str = ".") -> dict[str, Any]:
    """Find files by fuzzy filename matching."""

    try:
        if not pattern.strip():
            return {"success": False, "error": "pattern 不能为空。"}

        root = Path(path).expanduser().resolve()
        if not root.exists():
            return {"success": False, "error": f"路径不存在: {path}"}
        if not root.is_dir():
            return {"success": False, "error": f"不是目录: {path}"}

        needle = pattern.lower()
        matches = []
        for file_path in _iter_files(root):
            name = file_path.name.lower()
            if needle in name or fnmatch(name, needle):
                matches.append(str(file_path))
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break

        return {
            "success": True,
            "data": {
                "matches": matches,
                "count": len(matches),
                "truncated": len(matches) >= MAX_SEARCH_RESULTS,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def search_text(keyword: str, path: str = ".") -> dict[str, Any]:
    """Search for text in project files with bounded results."""

    try:
        if not keyword:
            return {"success": False, "error": "keyword 不能为空。"}

        root = Path(path).expanduser().resolve()
        if not root.exists():
            return {"success": False, "error": f"路径不存在: {path}"}
        if not root.is_dir():
            return {"success": False, "error": f"不是目录: {path}"}

        results = []
        lowered_keyword = keyword.lower()
        for file_path in _iter_files(root):
            if len(results) >= MAX_SEARCH_RESULTS:
                break
            if not _looks_text_file(file_path):
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as file:
                    for line_number, line in enumerate(file, start=1):
                        if lowered_keyword in line.lower():
                            results.append(
                                {
                                    "path": str(file_path),
                                    "line": line_number,
                                    "text": line.strip()[:300],
                                }
                            )
                            if len(results) >= MAX_SEARCH_RESULTS:
                                break
            except OSError:
                continue

        return {
            "success": True,
            "data": {
                "matches": results,
                "count": len(results),
                "truncated": len(results) >= MAX_SEARCH_RESULTS,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def _iter_files(root: Path):
    """Yield project files while skipping ignored directories."""

    for path in root.rglob("*"):
        if any(part in IGNORED_NAMES for part in path.parts):
            continue
        if path.is_file():
            yield path


def _looks_text_file(path: Path) -> bool:
    """Skip very large files and files that appear to be binary."""

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        sample = path.read_bytes()[:2048]
        return b"\x00" not in sample
    except OSError:
        return False


PROJECT_TOOLS = {
    "get_project_tree": get_project_tree,
    "find_files": find_files,
    "search_text": search_text,
}


PROJECT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_project_tree",
            "description": "返回项目目录树，自动忽略 .venv、.idea、__pycache__、.git 等目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要查看的项目目录，默认当前目录。",
                        "default": ".",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "目录树最大深度，默认 3。",
                        "default": 3,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "根据文件名 pattern 模糊查找项目文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "文件名关键词或通配符，例如 loop.py、*.py、readme。",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起点目录，默认当前目录。",
                        "default": ".",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "在项目文本文件中搜索关键词，返回路径、行号和匹配行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "要搜索的文本关键词。",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起点目录，默认当前目录。",
                        "default": ".",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
]

