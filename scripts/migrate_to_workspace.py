"""Copy legacy root stores into default workspace without deleting originals."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.workspace import WorkspaceManager  # noqa: E402


LEGACY_TO_WORKSPACE = {
    "document_store": "document_dir",
    "vector_store": "vector_dir",
}


def main() -> int:
    manager = WorkspaceManager()
    context = manager.get_context()
    copied = 0
    skipped = 0
    backups = 0

    for legacy_name, context_attr in LEGACY_TO_WORKSPACE.items():
        source_dir = PROJECT_ROOT / legacy_name
        target_dir = getattr(context, context_attr)
        target_dir.mkdir(parents=True, exist_ok=True)
        if not source_dir.exists():
            continue
        for source in sorted(source_dir.glob("*.json")):
            if source.name.startswith(".env"):
                skipped += 1
                continue
            target = target_dir / source.name
            if target.exists():
                backup = target.with_suffix(target.suffix + ".bak")
                index = 1
                while backup.exists():
                    backup = target.with_suffix(target.suffix + f".bak{index}")
                    index += 1
                shutil.copy2(target, backup)
                backups += 1
                skipped += 1
                print(f"skip_existing={target.relative_to(PROJECT_ROOT)} backup={backup.relative_to(PROJECT_ROOT)}")
                continue
            shutil.copy2(source, target)
            copied += 1
            print(f"copied={source.relative_to(PROJECT_ROOT)} -> {target.relative_to(PROJECT_ROOT)}")

    print("Migration summary")
    print(f"- workspace: {context.workspace_id}")
    print(f"- copied: {copied}")
    print(f"- skipped_existing_or_blocked: {skipped}")
    print(f"- backups_created: {backups}")
    print("- legacy data deleted: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
