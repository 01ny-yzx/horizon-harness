"""Artifact download routes."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core.file_output_policy import FileOutputPolicy


router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{artifact_id:path}")
def download_artifact(artifact_id: str) -> FileResponse:
    """Download one policy-created artifact by opaque artifact id."""

    if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    path = FileOutputPolicy().artifact_path_for_id(artifact_id)
    if path is None or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path=Path(path), media_type=media_type, filename=path.name)
