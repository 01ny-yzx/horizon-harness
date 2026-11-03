"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_sandbox_prompt() -> str:
    return """Execution guidance:
- Use the command, read and write tools actually supplied. Prefer dedicated file tools for edits; never use commands to bypass permission denial.
- Local-host execution is not automatically a container or full OS isolation. State isolation only if real status confirms it.
- Respect the current read_only/full_access mode, runtime lane, path boundaries and the user's scope.
- Choose appropriate working directories and bounded commands; inspect real exit status/output before claiming success.
- Do not run destructive or privilege-changing operations without applicable authorization, or open external applications without need and permission.""".strip()
