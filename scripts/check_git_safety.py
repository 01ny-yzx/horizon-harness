"""Check whether sensitive local files are protected from Git tracking."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_PATHS = [
    ".env",
    ".venv",
    "frontend/node_modules",
]
STORE_ROOTS = {"workspace_store", "cache_store", "usage_store", "sandbox_store"}
PLACEHOLDER_NAMES = {".gitkeep"}
KEY_PATTERNS = [
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{8,}")),
    ("tavily_key", re.compile(r"tvly-[A-Za-z0-9_\-]{6,}")),
    ("llm_env", re.compile(r"\bLLM_API_KEY\s*=")),
    ("deepseek_env", re.compile(r"\bDEEPSEEK_API_KEY\s*=")),
    ("tavily_env", re.compile(r"\bTAVILY_API_KEY\s*=")),
    ("embedding_env", re.compile(r"\bEMBEDDING_API_KEY\s*=")),
    ("api_keys_env", re.compile(r"\bAPI_KEYS\s*=")),
]
TEXT_SUFFIXES = {
    "",
    ".css",
    ".env",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yml",
    ".yaml",
}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "logs"}


def main() -> int:
    """Print a structured safety report and return non-zero on high risk."""

    report = build_report(PROJECT_ROOT)
    print(f"status={report['status']}")
    print("ignored_sensitive_files=" + json.dumps(report["ignored_sensitive_files"], ensure_ascii=False))
    print("risky_files=" + json.dumps(report["risky_files"], ensure_ascii=False))
    print("suggestions=" + json.dumps(report["suggestions"], ensure_ascii=False))
    return 1 if report["status"] == "error" else 0


def build_report(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build a Git safety report without changing files."""

    project_root = project_root.resolve()
    ignored_sensitive_files: list[dict[str, str]] = []
    risky_files: list[dict[str, str]] = []
    high_risk_count = 0
    suggestions: list[str] = []

    for relative in SENSITIVE_PATHS:
        path = project_root / relative
        if not path.exists():
            continue
        if _is_ignored(project_root, path):
            ignored_sensitive_files.append({"path": relative, "risk": "ignored_sensitive_path"})
        else:
            risky_files.append({"path": relative, "risk": "sensitive_path_not_ignored"})
            high_risk_count += 1

    for path in _iter_store_real_data(project_root):
        relative = _relative_posix(project_root, path)
        if _is_ignored(project_root, path):
            ignored_sensitive_files.append({"path": relative, "risk": "ignored_store_data"})
        else:
            risky_files.append({"path": relative, "risk": "store_data_not_ignored"})
            high_risk_count += 1

    for path in _iter_text_files(project_root):
        relative = _relative_posix(project_root, path)
        risks = _scan_secret_risks(path)
        if not risks:
            continue
        if _is_ignored(project_root, path):
            ignored_sensitive_files.append({"path": relative, "risk": "ignored_secret_like_text"})
        else:
            for risk in risks:
                risky_files.append({"path": relative, "risk": risk})
                if _is_high_risk_secret_file(relative):
                    high_risk_count += 1

    if high_risk_count:
        status = "error"
        suggestions.append("Update .gitignore before running git add.")
        suggestions.append("Move real keys into .env and keep only safe placeholders in .env.example.")
        suggestions.append("Do not commit store JSON data or sandbox/workspace runtime files.")
    elif risky_files:
        status = "warning"
        suggestions.append("Review suspicious key-like strings and keep real values out of committed files.")
    elif ignored_sensitive_files:
        status = "warning"
        suggestions.append("Sensitive local files exist but appear ignored; verify with git status before committing.")
    else:
        status = "ok"
        suggestions.append("No sensitive Git tracking risk detected.")

    return {
        "status": status,
        "ignored_sensitive_files": _dedupe_records(ignored_sensitive_files),
        "risky_files": _dedupe_records(risky_files),
        "suggestions": suggestions,
    }


def _iter_store_real_data(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for root_name in STORE_ROOTS:
        root = project_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name in PLACEHOLDER_NAMES:
                continue
            paths.append(path)
    return paths


def _iter_text_files(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(project_root).parts)
        if parts & SKIP_DIRS:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        paths.append(path)
    return paths


def _scan_secret_risks(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ["unreadable_text_file"]

    risks: list[str] = []
    for name, pattern in KEY_PATTERNS:
        if pattern.search(text):
            risks.append(f"secret_pattern:{name}")
    return risks


def _is_ignored(project_root: Path, path: Path) -> bool:
    git_result = _git_check_ignore(project_root, path)
    if git_result is not None:
        return git_result
    return _fallback_gitignore_match(project_root, path)


def _git_check_ignore(project_root: Path, path: Path) -> bool | None:
    if not (project_root / ".git").exists():
        return None

    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            cwd=project_root,
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def _is_high_risk_secret_file(relative: str) -> bool:
    if relative == ".env":
        return True
    if relative.startswith(".env.") and relative != ".env.example":
        return True
    if relative.startswith(("memory_store/", "document_store/", "vector_store/", "workspace_store/")):
        return True
    if relative.startswith(("cache_store/", "usage_store/", "sandbox_store/")):
        return True
    return False


def _fallback_gitignore_match(project_root: Path, path: Path) -> bool:
    gitignore = project_root / ".gitignore"
    if not gitignore.exists():
        return False

    relative = _relative_posix(project_root, path)
    ignored = False
    for raw_line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        if _match_gitignore_pattern(relative, pattern):
            ignored = not negated
    return ignored


def _match_gitignore_pattern(relative: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/").rstrip("/")
    if not normalized:
        return False
    if pattern.endswith("/"):
        return relative == normalized
    if pattern.endswith("/**"):
        prefix = normalized[:-3]
        return relative == prefix or relative.startswith(prefix + "/")
    return fnmatch.fnmatch(relative, normalized) or fnmatch.fnmatch(Path(relative).name, normalized)


def _relative_posix(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root).as_posix()


def _dedupe_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for record in records:
        key = (record["path"], record["risk"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
