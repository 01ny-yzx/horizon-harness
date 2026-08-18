"""Safe Git inspection tools for the Agent.

These tools intentionally avoid dangerous Git operations. They never push,
never reset hard, never clean, and never rebase. Every command uses
``subprocess.run`` with a list of arguments and ``shell=False``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


MAX_DIFF_CHARS = 12_000
MAX_LOG_LIMIT = 20
INIT_SUGGESTION = "Run: python scripts/init_git_repo.py"


def git_is_repo() -> dict[str, Any]:
    """Check whether the current directory is inside a Git work tree."""

    cwd = os.getcwd()
    result = _run_git(["rev-parse", "--is-inside-work-tree"])
    if result["returncode"] != 0:
        return {
            "success": True,
            "data": {
                "is_repo": False,
                "repo_root": "",
                "cwd": cwd,
                "stderr": result["stderr"],
                "suggestion": INIT_SUGGESTION,
            },
        }

    root = _repo_root()
    return {
        "success": True,
        "data": {
            "is_repo": result["stdout"].strip().lower() == "true",
            "repo_root": str(root) if root else "",
            "cwd": cwd,
        },
    }


def git_status() -> dict[str, Any]:
    """Return short Git status and parsed changed files."""

    repo = _require_repo()
    if not repo["success"]:
        return repo

    result = _run_git(["status", "--short"])
    if result["returncode"] != 0:
        return _failure("git status failed", result)

    files = _parse_status_files(result["stdout"])
    return {
        "success": True,
        "data": {
            "stdout": result["stdout"],
            "has_changes": bool(files),
            "files": files,
        },
    }


def git_diff(file_path: str | None = None) -> dict[str, Any]:
    """Return unstaged diff, optionally scoped to one repo file."""

    repo = _require_repo()
    if not repo["success"]:
        return repo

    args = ["diff", "--"]
    if file_path:
        safe = _safe_repo_path(file_path)
        if not safe["success"]:
            return safe
        args.append(str(safe["data"]["relative_path"]))
    else:
        args.append(".")

    result = _run_git(args)
    if result["returncode"] != 0:
        return _failure("git diff failed", result)

    diff_text, truncated = _truncate(result["stdout"], MAX_DIFF_CHARS)
    return {
        "success": True,
        "data": {
            "diff": diff_text,
            "changed": bool(result["stdout"].strip()),
            "truncated": truncated,
        },
    }


def git_diff_summary(file_path: str | None = None) -> dict[str, Any]:
    """Return compact diff summary for LLM context."""

    repo = _require_repo()
    if not repo["success"]:
        return repo

    args = ["diff", "--stat", "--"]
    if file_path:
        safe = _safe_repo_path(file_path)
        if not safe["success"]:
            return safe
        args.append(str(safe["data"]["relative_path"]))
    else:
        args.append(".")

    result = _run_git(args)
    if result["returncode"] != 0:
        return _failure("git diff --stat failed", result)

    files_changed = _parse_diff_stat_files(result["stdout"])
    summary, truncated = _truncate(result["stdout"], 4_000)
    return {
        "success": True,
        "data": {
            "summary": summary,
            "files_changed": files_changed,
            "truncated": truncated,
        },
    }


def git_log(limit: int = 5) -> dict[str, Any]:
    """Return recent commit log."""

    repo = _require_repo()
    if not repo["success"]:
        return repo

    safe_limit = max(1, min(int(limit), MAX_LOG_LIMIT))
    result = _run_git(["log", "--oneline", "-n", str(safe_limit)])
    if result["returncode"] != 0:
        return _failure("git log failed", result)

    return {"success": True, "data": {"stdout": result["stdout"], "limit": safe_limit}}


def git_restore_file(file_path: str) -> dict[str, Any]:
    """Restore unstaged changes for one explicit tracked file."""

    if not file_path or file_path.strip() in {".", "*"}:
        return {"success": False, "error": "Only one explicit file path can be restored."}

    safe = _safe_repo_path(file_path)
    if not safe["success"]:
        return safe

    result = _run_git(["restore", "--", str(safe["data"]["relative_path"])])
    if result["returncode"] != 0:
        return _failure("git restore failed", result)

    return {
        "success": True,
        "data": {
            "path": str(safe["data"]["relative_path"]),
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
    }


def git_add(file_paths: list[str]) -> dict[str, Any]:
    """Stage explicit files only."""

    if not isinstance(file_paths, list) or not file_paths:
        return {"success": False, "error": "file_paths must be a non-empty list[str]."}

    relative_paths = []
    for file_path in file_paths:
        if not isinstance(file_path, str) or not file_path.strip():
            return {"success": False, "error": "file_paths contains an invalid path."}
        if file_path.strip() in {".", "*"}:
            return {"success": False, "error": "git add . and wildcard staging are not allowed."}
        safe = _safe_repo_path(file_path)
        if not safe["success"]:
            return safe
        relative_paths.append(str(safe["data"]["relative_path"]))

    result = _run_git(["add", "--", *relative_paths])
    if result["returncode"] != 0:
        return _failure("git add failed", result)

    return {
        "success": True,
        "data": {
            "files": relative_paths,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
    }


def git_commit(message: str) -> dict[str, Any]:
    """Create a local commit only when explicitly enabled by environment."""

    suggested = (message or "").strip()
    if not suggested:
        return {"success": False, "error": "commit message cannot be empty."}

    if os.getenv("AGENT_ALLOW_GIT_COMMIT", "").lower() != "true":
        return {
            "success": False,
            "error": "Automatic commit is disabled. Set AGENT_ALLOW_GIT_COMMIT=true to enable it.",
            "data": {"suggested_message": suggested},
        }

    repo = _require_repo()
    if not repo["success"]:
        return repo

    result = _run_git(["commit", "-m", suggested])
    if result["returncode"] != 0:
        return _failure("git commit failed", result, {"suggested_message": suggested})

    return {
        "success": True,
        "data": {
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "message": suggested,
        },
    }


def git_suggest_commit_message() -> dict[str, Any]:
    """Suggest a concise local commit message from current changes."""

    repo = _require_repo()
    if not repo["success"]:
        return repo

    result = _run_git(["status", "--short"])
    if result["returncode"] != 0:
        return _failure("git status failed", result)

    files = _parse_status_files(result["stdout"])
    paths = [file["path"] for file in files]
    if not paths:
        message = "chore: no local changes"
    elif any(path.startswith("README") or path.endswith(".md") for path in paths):
        message = "docs: update project documentation"
    elif any(path.startswith("evals/") or path.startswith("scripts/") for path in paths):
        message = "chore: improve project tooling"
    elif any(path.startswith("frontend/") for path in paths):
        message = "feat: update frontend"
    elif any(path.startswith("api/") for path in paths):
        message = "feat: update api"
    else:
        message = "chore: update agent project"

    return {"success": True, "data": {"message": message, "files": files}}


def _run_git(args: list[str]) -> dict[str, Any]:
    """Run a git command safely."""

    try:
        completed = subprocess.run(
            ["git", *args],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "git command was not found."}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "Git command timed out."}
    except Exception as exc:  # noqa: BLE001
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def _repo_root() -> Path | None:
    """Return repository root or None."""

    result = _run_git(["rev-parse", "--show-toplevel"])
    if result["returncode"] != 0:
        return None
    return Path(result["stdout"].strip()).resolve()


def _require_repo() -> dict[str, Any]:
    """Return failure JSON if cwd is not in a Git repository."""

    root = _repo_root()
    if root is None:
        return {
            "success": False,
            "error": "Current directory is not a Git repository.",
            "data": {"suggestion": INIT_SUGGESTION},
        }
    return {"success": True, "data": {"repo_root": str(root), "cwd": os.getcwd()}}


def _safe_repo_path(file_path: str) -> dict[str, Any]:
    """Validate that a path stays inside the repository root."""

    root = _repo_root()
    if root is None:
        return {
            "success": False,
            "error": "Current directory is not a Git repository.",
            "data": {"suggestion": INIT_SUGGESTION},
        }

    candidate = Path(file_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(os.getcwd()) / candidate
    resolved = candidate.resolve()

    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return {"success": False, "error": f"Path is outside the Git repository: {file_path}"}

    if str(relative) in {"", "."}:
        return {"success": False, "error": "Provide an explicit file path inside the repository."}

    return {
        "success": True,
        "data": {
            "repo_root": str(root),
            "path": str(resolved),
            "relative_path": relative,
        },
    }


def _parse_status_files(stdout: str) -> list[dict[str, str]]:
    """Parse git status --short output."""

    files = []
    for line in stdout.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[1]
        files.append({"status": status.strip(), "path": path})
    return files


def _parse_diff_stat_files(stdout: str) -> list[str]:
    """Extract file paths from git diff --stat output."""

    files = []
    for line in stdout.splitlines():
        if "|" not in line:
            continue
        files.append(line.split("|", maxsplit=1)[0].strip())
    return files


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Truncate long text."""

    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}\n... [truncated {len(text) - limit} chars]", True


def _failure(message: str, result: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a structured failure response."""

    data = {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
    }
    if extra:
        data.update(extra)
    return {"success": False, "error": message, "data": data}


GIT_TOOLS = {
    "git_is_repo": git_is_repo,
    "git_status": git_status,
    "git_diff": git_diff,
    "git_diff_summary": git_diff_summary,
    "git_log": git_log,
    "git_restore_file": git_restore_file,
    "git_add": git_add,
    "git_commit": git_commit,
    "git_suggest_commit_message": git_suggest_commit_message,
}


GIT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "git_is_repo",
            "description": "Check whether the current directory is a Git repository.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show current Git status and changed files.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show unstaged diff, optionally for one repository file.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "Optional repository file path."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff_summary",
            "description": "Show compact diff summary for local changes.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "Optional repository file path."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent Git commits, up to 20.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Commit count.", "default": 5}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_restore_file",
            "description": "Restore unstaged changes for one explicit tracked file.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "Repository file path to restore."}},
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_add",
            "description": "Stage explicit files only; git add . is not allowed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit repository file paths to stage.",
                    }
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Create a local commit only when AGENT_ALLOW_GIT_COMMIT=true.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Commit message."}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_suggest_commit_message",
            "description": "Suggest a concise commit message from current local changes.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
