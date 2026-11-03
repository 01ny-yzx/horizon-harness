"""Python tool subprocesses work from both source and standalone executables."""
import sys


def python_command(script: str) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--_horizon-python', str(script)]
    return [sys.executable, str(script)]
