"""Guard that the repository does not track Markdown documentation."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def tracked_markdown_files(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    tracked = tracked_markdown_files()
    if tracked:
        print("status=warning tracked_markdown_docs_present=true")
        for path in tracked:
            print(f"tracked={path}")
        return 1
    print("status=ok tracked_markdown_docs_present=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
