"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_workspace_prompt() -> str:
    return """Workspace context:
- Use only the current user's/project's workspace records and runtime-provided directory.
- A Session-bound agent must not change user/project identity to access another workspace.
- Workspace context is implementation detail: do not volunteer default IDs, paths or storage internals in ordinary replies.
- Do not invent state for another user or project.""".strip()
