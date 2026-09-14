from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Cookie, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from x_follow_list.api.dependencies import authenticate_request
from x_follow_list.application.auth import SESSION_COOKIE_NAME, AuthService
from x_follow_list.application.binding import BindingSession, BrowserBindingService
from x_follow_list.persistence.database import Database

router = APIRouter(prefix="/api/v1/x-account-bind-sessions", tags=["x-account-binding"])


class CreateBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_config_id: str | None = Field(default=None, min_length=1, max_length=255)
    profile_ref: str | None = Field(default=None, min_length=1, max_length=255)
    x_account_id: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def select_new_binding_or_revalidation(self) -> Self:
        creates_new = self.provider_config_id is not None and self.profile_ref is not None
        revalidates = self.x_account_id is not None
        if creates_new == revalidates:
            raise ValueError("select either provider/profile or an account to revalidate")
        if not creates_new and not revalidates:
            raise ValueError("binding target is required")
        if revalidates and (self.provider_config_id is not None or self.profile_ref is not None):
            raise ValueError("revalidation cannot replace the bound provider profile")
        return self


class DetectedIdentityResponse(BaseModel):
    x_user_id: str
    username: str | None
    display_name: str | None


class BindingResponse(BaseModel):
    id: str
    owner_user_id: str
    provider_config_id: str
    provider_code: str
    provider_config_version: int
    profile_ref: str
    target_account_id: str | None
    status: str
    detected_identity: DetectedIdentityResponse | None
    account_id: str | None
    error_code: str | None
    expires_at: datetime


def _services(request: Request) -> tuple[Database, AuthService, BrowserBindingService]:
    return (
        request.app.state.database,
        request.app.state.auth_service,
        request.app.state.browser_binding_service,
    )


def _response(binding: BindingSession) -> BindingResponse:
    identity = None
    if binding.detected_x_user_id is not None:
        identity = DetectedIdentityResponse(
            x_user_id=binding.detected_x_user_id,
            username=binding.detected_username,
            display_name=binding.detected_display_name,
        )
    return BindingResponse(
        id=binding.session_id,
        owner_user_id=binding.owner_user_id,
        provider_config_id=binding.provider_config_id,
        provider_code=binding.provider_code,
        provider_config_version=binding.provider_config_version,
        profile_ref=binding.profile_ref,
        target_account_id=binding.target_account_id,
        status=binding.status,
        detected_identity=identity,
        account_id=binding.confirmed_account_id,
        error_code=binding.error_code,
        expires_at=binding.expires_at,
    )


@router.post("", status_code=202, response_model=BindingResponse)
async def create_binding(
    payload: CreateBindingRequest,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> BindingResponse:
    user = await authenticate_request(
        request, session_token, csrf_token, mutation=True
    )
    _database, _auth_service, binding_service = _services(request)
    if payload.x_account_id is not None:
        binding = await binding_service.create_revalidation(
            user.user_id, payload.x_account_id
        )
    else:
        assert payload.provider_config_id is not None
        assert payload.profile_ref is not None
        binding = await binding_service.create(
            user.user_id, payload.provider_config_id, payload.profile_ref
        )
    return _response(binding)


@router.get("/{binding_id}", response_model=BindingResponse)
async def get_binding(
    binding_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> BindingResponse:
    user = await authenticate_request(request, session_token)
    _database, _auth_service, binding_service = _services(request)
    return _response(await binding_service.get(user.user_id, binding_id))


@router.post("/{binding_id}/confirm", response_model=BindingResponse)
async def confirm_binding(
    binding_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> BindingResponse:
    user = await authenticate_request(
        request, session_token, csrf_token, mutation=True
    )
    _database, _auth_service, binding_service = _services(request)
    await binding_service.confirm(user.user_id, binding_id)
    return _response(await binding_service.get(user.user_id, binding_id))


@router.post("/{binding_id}/cancel", status_code=204, response_model=None)
async def cancel_binding(
    binding_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    user = await authenticate_request(
        request, session_token, csrf_token, mutation=True
    )
    _database, _auth_service, binding_service = _services(request)
    await binding_service.cancel(user.user_id, binding_id)
    return Response(status_code=204)
