"""Static and FastAPI route smoke for removal of direct command surfaces."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.schemas as schemas
from api.app import app


def main() -> None:
    routes = {getattr(route, "path", "") for route in app.routes}
    assert "/sandbox/status" in routes
    assert routes.isdisjoint({"/sandbox/run-shell", "/sandbox/run-python", "/sandbox/cleanup"})
    assert not hasattr(schemas, "SandboxShellRequest")
    assert not hasattr(schemas, "SandboxPythonRequest")

    frontend = ROOT / "frontend" / "src"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in frontend.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx"}
    )
    for forbidden in (
        "SandboxPanel",
        '"sandbox" |',
        'id: "sandbox"',
        "runSandboxShell",
        "runSandboxPython",
        "cleanupSandbox",
        "sandboxStatus",
        "/sandbox/run-shell",
        "/sandbox/run-python",
    ):
        assert forbidden not in source, forbidden
    assert "ChatWindow" in source
    assert 'post<ChatResponse>("/chat"' in source
    print("smoke_sandbox_manual_surface_removed: PASS")


if __name__ == "__main__":
    main()
