from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Header, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from x_follow_list.api.dependencies import authenticate_request
from x_follow_list.application.auth import SESSION_COOKIE_NAME
from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.artifacts.xlsx import MIME_TYPE, XlsxArtifactService
from x_follow_list.persistence.authorization import OwnedResourceRepository

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


class ArtifactResponse(BaseModel):
    id: str
    scan_run_id: str
    status: str
    sha256: str | None
    byte_size: int | None
    expires_at: datetime
    deleted_at: datetime | None
    download_url: str


@router.post("/scan-runs/{run_id}/artifacts/xlsx", response_model=ArtifactResponse)
async def build_xlsx_artifact(
    run_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> ArtifactResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    database = request.app.state.database
    async with database.session() as session:
        await OwnedResourceRepository(session).require_scan_run(user.user_id, run_id)
    artifact_service: XlsxArtifactService = request.app.state.xlsx_artifact_service
    try:
        artifact = await artifact_service.build(run_id)
    except (ApplicationError, ResourceNotFoundError):
        raise
    except Exception:
        raise ApplicationError(
            "ARTIFACT_GENERATION_FAILED", "XLSX artifact generation failed", 500
        ) from None
    artifact_id = str(artifact["id"])
    return ArtifactResponse(
        **artifact,
        download_url=f"/api/v1/artifacts/{artifact_id}/download",
    )


@router.get("/artifacts/{artifact_id}/download", response_class=FileResponse)
async def download_artifact(
    artifact_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> FileResponse:
    user = await authenticate_request(request, session_token)
    database = request.app.state.database
    async with database.session() as session:
        artifact = await OwnedResourceRepository(session).require_artifact(
            user.user_id, artifact_id
        )
    if not _is_downloadable(artifact):
        raise ResourceNotFoundError
    path, storage_root, exists = await asyncio.to_thread(
        _resolve_artifact_path,
        str(artifact["storage_path"]),
        request.app.state.settings.data_dir,
    )
    if storage_root not in path.parents or not exists:
        raise ResourceNotFoundError
    response = FileResponse(
        path,
        media_type=MIME_TYPE,
        filename=f"x-follow-list-{artifact['scan_run_id']}.xlsx",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _is_downloadable(artifact: Mapping[str, Any]) -> bool:
    expires_at = artifact["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return bool(
        artifact["kind"] == "XLSX"
        and artifact["status"] == "READY"
        and artifact["deleted_at"] is None
        and expires_at > datetime.now(expires_at.tzinfo)
    )


def _resolve_artifact_path(raw_path: str, data_dir: Path) -> tuple[Path, Path, bool]:
    path = Path(raw_path).resolve()
    storage_root = (data_dir / "artifacts").resolve()
    return path, storage_root, path.is_file()
