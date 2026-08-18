"""Sandbox prompt rules."""

from __future__ import annotations


def build_sandbox_prompt() -> str:
    """Return sandbox execution guidance."""

    return """
Sandbox / Docker rules:
1. Command execution must use a currently supplied command tool.
2. Command observations include bounded output and execution status.
3. For Python, Node, Java, C, Go, Rust, or other languages, use a currently supplied command tool when execution is needed.
4. File reading and writing must use currently supplied file tools.
5. Do not use shell redirection, cat > file, or inline language code to bypass file write policy.
6. Local sandbox runs in workspace_store/<user>/<project>/sandbox. It limits cwd, timeout, output, environment variables, and sensitive paths, but it is not full OS isolation.
7. Docker sandbox is stronger isolation when Docker is installed. If Docker is unavailable, say that only restricted local sandbox is available.
8. Refuse deletion, formatting, shutdown, privilege changes, permission destruction, and network download-and-execute commands.
9. Never read .env, API keys, memory_store, document_store, vector_store, workspace_store data outside sandbox_dir, or system directories.
10. When a command is refused, report the policy reason and suggest a safer sandbox command.
11. File writes must go through File Output Policy and Agent access mode.
12. In read_only mode, file writes and other side effects are not allowed.
13. In full_access mode, only current-turn authorized, non-sensitive, non-dangerous paths may be written; otherwise suggest AGENT_DEFAULT_OUTPUT_DIR or a clear non-sensitive local path.
14. Never run open, explorer, xdg-open, AppleScript, or subprocess launchers to open an output directory. The write_file result provides open_directory for the client UI.
""".strip()
