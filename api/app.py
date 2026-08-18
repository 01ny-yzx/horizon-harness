"""FastAPI application factory for Service API v1."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.deps import require_api_key
from api.routes import browser, cache, chat, cloud_mcp_gateway, documents, files, health, mcp, mcp_gateway, memory, rag, sandbox, usage, workspace
from config.settings import settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI app."""

    app = FastAPI(title="Command Line AI Agent API", version="0.1.0", debug=settings.api_debug)
    allow_origins = (
        ["*"]
        if settings.api_cors_allow_all
        else [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    protected = [Depends(require_api_key)]
    app.include_router(chat.router, dependencies=protected)
    app.include_router(workspace.router, dependencies=protected)
    app.include_router(memory.router, dependencies=protected)
    app.include_router(documents.router, dependencies=protected)
    app.include_router(rag.router, dependencies=protected)
    app.include_router(usage.router, dependencies=protected)
    app.include_router(cache.router, dependencies=protected)
    app.include_router(sandbox.router, dependencies=protected)
    app.include_router(browser.router, dependencies=protected)
    app.include_router(files.router, dependencies=protected)
    app.include_router(cloud_mcp_gateway.router, dependencies=protected)
    app.include_router(mcp_gateway.router, dependencies=protected)
    app.include_router(mcp.router, dependencies=protected)
    return app


app = create_app()
