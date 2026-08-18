"""Path safety policy for coding workflow modifications."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CodingSafetyPolicy:
    """Default protected path policy for code modification tasks."""

    protected_paths: tuple[str, ...] = (
        ".env",
        ".env.local",
        ".env.production",
        ".git/",
        "stores/",
        "node_modules/",
        ".venv/",
        "venv/",
        "__pycache__/",
        "dist/",
        "build/",
    )
    generated_paths: tuple[str, ...] = ("__pycache__/", "dist/", "build/")
    dependency_paths: tuple[str, ...] = ("node_modules/", ".venv/", "venv/")
    require_git_status: bool = True
    require_plan_before_modify: bool = True
    require_diff_after_modify: bool = True
    require_tests_after_modify: bool = True

    def normalize_path(self, path: str) -> str:
        """Normalize path separators for platform-neutral matching."""

        normalized = (path or "").replace("\\", "/").strip()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def is_protected_path(self, path: str) -> bool:
        """Return True when path is protected from modification."""

        normalized = self.normalize_path(path).lower()
        if not normalized:
            return False
        for protected in self.protected_paths:
            protected_norm = self.normalize_path(protected).lower()
            if protected_norm.endswith("/"):
                prefix = protected_norm.rstrip("/")
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return True
            elif normalized == protected_norm:
                return True
        return False

    def should_warn_before_modify(self, path: str) -> bool:
        """Return True when a modification needs an explicit safety warning."""

        return self.is_protected_path(path)

    def explain_path_risk(self, path: str) -> str:
        """Explain why a path is risky or safe to modify."""

        normalized = self.normalize_path(path)
        if self.is_protected_path(path):
            return f"{normalized} is protected and should not be modified by the coding workflow."
        return f"{normalized} is not protected by the default coding safety policy."
