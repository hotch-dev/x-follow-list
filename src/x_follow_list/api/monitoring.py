from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Header, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from x_follow_list.api.dependencies import authenticate_request
from x_follow_list.application.auth import SESSION_COOKIE_NAME, AuthService
from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.application.monitoring import MonitoringQueryService
from x_follow_list.artifacts.xlsx import MIME_TYPE, XlsxArtifactService
from x_follow_list.persistence.authorization import OwnedResourceRepository
from x_follow_list.persistence.database import Database

router = APIRouter(prefix="/api/v1", tags=["x-relationship-monitoring"])


class AccountResponse(BaseModel):
    id: str
    x_user_id: str
    username: str | None
    display_name: str | None
    session_status: str
    provider_code: str
    profile_ref: str
    version: int
    last_successful_scan_at: datetime | None


class AccountListResponse(BaseModel):
    items: list[AccountResponse]


class ErrorSummary(BaseModel):
    code: str
    summary: str


class ScanResponse(BaseModel):
    id: str
    x_account_id: str
    status: str
    progress_stage: str | None
    error: ErrorSummary | None
    follower_count: int | None
    following_count: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_successful_scan_at: datetime | None


class ScanListResponse(BaseModel):
    items: list[ScanResponse]
    next_cursor: str | None = None


class RelationshipResponse(BaseModel):
    x_user_id: str
    username: str | None
    display_name: str | None
    state: str
    non_followback_streak: int
    snapshot_id: str


class RelationshipListResponse(BaseModel):
    items: list[RelationshipResponse]
    next_cursor: str | None = None


class EventResponse(BaseModel):
    id: str
    x_account_id: str
    scan_run_id: str
    subject_x_user_id: str
    username: str | None = None
    display_name: str | None = None
    category: str
    event_type: str
    status: str
    version: int
    created_at: datetime
    acknowledged_at: datetime | None = None


class EventListResponse(BaseModel):
    items: list[EventResponse]
    next_cursor: str | None = None


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class UnbindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    delete_history: bool = False


class ArtifactResponse(BaseModel):
    id: str
    scan_run_id: str
    status: str
    sha256: str | None
    byte_size: int | None
    expires_at: datetime
    deleted_at: datetime | None
    download_url: str


def _services(request: Request) -> tuple[Database, AuthService, MonitoringQueryService]:
    return (
        request.app.state.database,
        request.app.state.auth_service,
        request.app.state.monitoring_query_service,
    )


def _scan(row: dict[str, Any]) -> ScanResponse:
    error = None
    if row.get("error_code") is not None:
        error = ErrorSummary(code=str(row["error_code"]), summary=str(row["error_summary"]))
    return ScanResponse(
        id=str(row["id"]),
        x_account_id=str(row["x_account_id"]),
        status=str(row["status"]),
        progress_stage=row.get("progress_stage"),
        error=error,
        follower_count=row.get("follower_count"),
        following_count=row.get("following_count"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        last_successful_scan_at=row.get("last_successful_scan_at"),
    )


@router.get("/x-accounts", response_model=AccountListResponse)
async def list_accounts(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AccountListResponse:
    user = await authenticate_request(request, session_token)
    _database, _auth, service = _services(request)
    rows = await service.list_accounts(user.user_id)
    return AccountListResponse(
        items=[
            AccountResponse(
                id=str(row["id"]),
                x_user_id=str(row["x_user_id"]),
                username=row["username"],
                display_name=row["display_name"],
                session_status=str(row["status"]),
                provider_code=str(row["provider_code"]),
                profile_ref=str(row["profile_ref"]),
                version=int(row["version"]),
                last_successful_scan_at=row["last_successful_scan_at"],
            )
            for row in rows
        ]
    )


@router.post("/x-accounts/{account_id}/scan-runs", status_code=202, response_model=ScanResponse)
async def create_scan(
    account_id: str,
    request: Request,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> ScanResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    _database, _auth, service = _services(request)
    return _scan(
        await service.enqueue_scan(
            user.user_id, account_id, idempotency_key, request.state.request_id
        )
    )


@router.get("/scan-runs", response_model=ScanListResponse)
async def list_scans(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> ScanListResponse:
    user = await authenticate_request(request, session_token)
    _database, _auth, service = _services(request)
    rows, next_cursor = await service.list_scans(user.user_id, limit, cursor)
    return ScanListResponse(
        items=[_scan(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/scan-runs/{run_id}", response_model=ScanResponse)
async def get_scan(
    run_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> ScanResponse:
    user = await authenticate_request(request, session_token)
    _database, _auth, service = _services(request)
    return _scan(await service.get_scan(user.user_id, run_id))


@router.post("/scan-runs/{run_id}/artifacts/xlsx", response_model=ArtifactResponse)
async def build_xlsx_artifact(
    run_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> ArtifactResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    database, _auth, _service = _services(request)
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
    database, _auth, _service = _services(request)
    async with database.session() as session:
        artifact = await OwnedResourceRepository(session).require_artifact(
            user.user_id, artifact_id
        )
    expires_at = artifact["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if (
        artifact["kind"] != "XLSX"
        or artifact["status"] != "READY"
        or artifact["deleted_at"] is not None
        or expires_at <= datetime.now(expires_at.tzinfo)
    ):
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


def _resolve_artifact_path(raw_path: str, data_dir: Path) -> tuple[Path, Path, bool]:
    path = Path(raw_path).resolve()
    storage_root = (data_dir / "artifacts").resolve()
    return path, storage_root, path.is_file()


@router.get("/relationships", response_model=RelationshipListResponse)
async def list_relationships(
    request: Request,
    x_account_id: Annotated[str, Query(min_length=1, max_length=255)],
    state: Annotated[
        Literal["MUTUAL", "NOT_FOLLOWING_BACK", "FOLLOWS_ME_ONLY", "ABSENT"] | None,
        Query(),
    ] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> RelationshipListResponse:
    user = await authenticate_request(request, session_token)
    _database, _auth, service = _services(request)
    rows, next_cursor = await service.list_relationships(
        user.user_id,
        x_account_id,
        state=state,
        search=search,
        limit=limit,
        cursor=cursor,
    )
    return RelationshipListResponse(
        items=[RelationshipResponse(**row) for row in rows], next_cursor=next_cursor
    )


@router.get("/relationship-events", response_model=EventListResponse)
async def list_events(
    request: Request,
    x_account_id: Annotated[str, Query(min_length=1, max_length=255)],
    status: Annotated[Literal["NEW", "ACKNOWLEDGED", "RESOLVED"] | None, Query()] = None,
    category: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    event_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> EventListResponse:
    user = await authenticate_request(request, session_token)
    _database, _auth, service = _services(request)
    rows, next_cursor = await service.list_events(
        user.user_id,
        x_account_id,
        status=status,
        category=category,
        event_type=event_type,
        limit=limit,
        cursor=cursor,
    )
    return EventListResponse(
        items=[EventResponse(**row) for row in rows], next_cursor=next_cursor
    )


@router.post("/relationship-events/{event_id}/acknowledge", response_model=EventResponse)
async def acknowledge_event(
    event_id: str,
    payload: AcknowledgeRequest,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> EventResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    _database, _auth, service = _services(request)
    return EventResponse(**await service.acknowledge_event(user.user_id, event_id, payload.version))


@router.delete("/x-accounts/{account_id}", status_code=204)
async def unbind_account(
    account_id: str,
    payload: UnbindRequest,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    if payload.delete_history:
        raise ApplicationError(
            "HISTORY_DELETE_DEFERRED", "History deletion is not available in this phase", 409
        )
    _database, _auth, service = _services(request)
    await service.unbind_account(
        user.user_id, account_id, payload.version, request.state.request_id
    )
    return Response(status_code=204)
