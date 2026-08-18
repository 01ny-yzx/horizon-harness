"""Workspace isolation prompt rules."""

from __future__ import annotations


def build_workspace_prompt() -> str:
    """Return user/project workspace rules."""

    return """
Workspace rules:
1. The Agent runs inside one user/project workspace.
2. Memories, documents, chunks, vectors, and traces belong only to the current workspace.
3. Do not search or infer data from another workspace.
4. After switch_workspace, use the new workspace memory/document/vector stores.
5. Do not fabricate data for another user or project.
6. The default workspace is default_user/default_project.
""".strip()
