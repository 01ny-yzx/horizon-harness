"""Run the FastAPI service for local development."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from config.settings import settings  # noqa: E402


def main() -> int:
    """Start uvicorn with configured host and port."""

    uvicorn.run("api.app:app", host=settings.api_host, port=settings.api_port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
