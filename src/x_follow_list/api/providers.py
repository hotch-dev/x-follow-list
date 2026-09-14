from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Cookie, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from x_follow_list.api.dependencies import authenticate_request
from x_follow_list.application.auth import SESSION_COOKIE_NAME
from x_follow_list.application.provider_configs import ProviderConfigService
from x_follow_list.browser.contracts import BrowserCapabilities
from x_follow_list.browser.registry import ProviderDescriptor

router = APIRouter(prefix="/api/v1", tags=["browser-providers"])


class ProviderCapabilitiesResponse(BaseModel):
    persistent_profiles: bool
    visible_login: bool
    remote_cdp: bool
    attach_existing_session: bool


class ProviderDescriptorResponse(BaseModel):
    code: str
    version: str
    capabilities: ProviderCapabilitiesResponse


class ProviderListResponse(BaseModel):
    items: list[ProviderDescriptorResponse]


class CreateProviderConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_code: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    config_version: int = Field(ge=1)
    config: dict[str, object]
    secret_ref: str | None = Field(default=None, max_length=255)


class ProviderConfigResponse(BaseModel):
    id: str
    provider_code: str
    config_version: int
    display_name: str
    has_secret: bool
    created_at: datetime
    updated_at: datetime


class ProviderConfigListResponse(BaseModel):
    items: list[ProviderConfigResponse]


class CapabilityReportResponse(BaseModel):
    provider_code: str
    provider_version: str
    capabilities: ProviderCapabilitiesResponse


class ProfileResponse(BaseModel):
    profile_ref: str
    display_name: str
    is_running: bool


class ProfileListResponse(BaseModel):
    items: list[ProfileResponse]


def _service(request: Request) -> ProviderConfigService:
    return cast(ProviderConfigService, request.app.state.provider_config_service)


@router.get("/browser-providers", response_model=ProviderListResponse)
async def list_providers(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> ProviderListResponse:
    await authenticate_request(request, session_token)
    items = [_descriptor(item) for item in _service(request).descriptors()]
    return ProviderListResponse(items=items)


@router.post(
    "/browser-provider-configs", status_code=201, response_model=ProviderConfigResponse
)
async def create_provider_config(
    payload: CreateProviderConfigRequest,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> ProviderConfigResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    row = await _service(request).create(user.user_id, **payload.model_dump())
    return ProviderConfigResponse(**row)


@router.get("/browser-provider-configs", response_model=ProviderConfigListResponse)
async def list_provider_configs(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> ProviderConfigListResponse:
    user = await authenticate_request(request, session_token)
    return ProviderConfigListResponse(
        items=[
            ProviderConfigResponse(**row)
            for row in await _service(request).list_configs(user.user_id)
        ]
    )


@router.post(
    "/browser-provider-configs/{config_id}/test",
    response_model=CapabilityReportResponse,
)
async def test_provider_config(
    config_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> CapabilityReportResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    report = await _service(request).validate(user.user_id, config_id)
    return CapabilityReportResponse(
        provider_code=report.provider_code,
        provider_version=report.provider_version,
        capabilities=_capabilities(report.capabilities),
    )


@router.get(
    "/browser-provider-configs/{config_id}/profiles", response_model=ProfileListResponse
)
async def list_profiles(
    config_id: str,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> ProfileListResponse:
    user = await authenticate_request(request, session_token)
    profiles = await _service(request).profiles(user.user_id, config_id)
    return ProfileListResponse(
        items=[
            ProfileResponse(
                profile_ref=item.profile_ref,
                display_name=item.display_name,
                is_running=item.is_running,
            )
            for item in profiles
        ]
    )


def _capabilities(value: BrowserCapabilities) -> ProviderCapabilitiesResponse:
    return ProviderCapabilitiesResponse(
        persistent_profiles=value.persistent_profiles,
        visible_login=value.visible_login,
        remote_cdp=value.remote_cdp,
        attach_existing_session=value.attach_existing_session,
    )


def _descriptor(value: ProviderDescriptor) -> ProviderDescriptorResponse:
    return ProviderDescriptorResponse(
        code=value.code,
        version=value.version,
        capabilities=_capabilities(value.capabilities),
    )
