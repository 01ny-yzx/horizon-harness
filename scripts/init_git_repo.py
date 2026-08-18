"""Safely initialize a Git repository for this project."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Initialize Git and run the safety checker."""

    parser = argparse.ArgumentParser(description="Safely initialize the project Git repository.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without running git init.")
    args = parser.parse_args(argv)

    if _is_git_repo(PROJECT_ROOT):
        print("git_repo=already_initialized")
    elif args.dry_run:
        print("git_repo=not_initialized")
        print("dry_run=true")
        print("would_run=git init")
    else:
        init_result = _run(["git", "init"], PROJECT_ROOT)
        if init_result.returncode != 0:
            print("git_repo=init_failed")
            print(init_result.stderr.strip() or init_result.stdout.strip())
            return 1
        print("git_repo=initialized")

    safety_result = _run([sys.executable, str(PROJECT_ROOT / "scripts" / "check_git_safety.py")], PROJECT_ROOT)
    if safety_result.stdout:
        print(safety_result.stdout.strip())
    if safety_result.stderr:
        print(safety_result.stderr.strip())

    if safety_result.returncode != 0:
        print("next_step=fix .gitignore or remove risky files before git add")
        return safety_result.returncode

    print("next_step=you can run:")
    print("  git add .")
    print('  git commit -m "initial stable agent project"')
    return 0


def _is_git_repo(cwd: Path) -> bool:
    result = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd)
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


if __name__ == "__main__":
    raise SystemExit(main())
