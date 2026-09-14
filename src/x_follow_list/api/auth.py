from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from x_follow_list.application.auth import SESSION_COOKIE_NAME, AuthService
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=12, max_length=1024)
    bootstrap_token: SecretStr = Field(min_length=16, max_length=1024)

    @field_validator("login")
    @classmethod
    def login_must_not_have_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("login must not have surrounding whitespace")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    login: str
    role: str


class SessionResponse(BaseModel):
    user: UserResponse
    csrf_token: str


def _services(request: Request) -> tuple[Database, AuthService, Settings]:
    return (
        request.app.state.database,
        request.app.state.auth_service,
        request.app.state.settings,
    )


@router.post("/bootstrap", status_code=201, response_model=UserResponse)
async def bootstrap_owner(payload: BootstrapRequest, request: Request) -> UserResponse:
    database, auth_service, _settings = _services(request)
    async with database.session() as session:
        user = await auth_service.bootstrap_owner(
            session,
            login=payload.login,
            password=payload.password.get_secret_value(),
            bootstrap_token=payload.bootstrap_token.get_secret_value(),
            request_id=request.state.request_id,
        )
    return UserResponse(id=user.user_id, login=user.login, role=user.role)


@router.post("/session", status_code=201, response_model=SessionResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> SessionResponse:
    database, auth_service, settings = _services(request)
    async with database.session() as session:
        created = await auth_service.create_session(
            session,
            login=payload.login,
            password=payload.password.get_secret_value(),
            request_id=request.state.request_id,
        )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=created.token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.environment is RuntimeEnvironment.PRODUCTION,
        samesite="strict",
        path="/",
    )
    return SessionResponse(
        user=UserResponse(
            id=created.user.user_id,
            login=created.user.login,
            role=created.user.role,
        ),
        csrf_token=created.csrf_token,
    )


@router.get("/session", response_model=UserResponse)
async def current_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> UserResponse:
    database, auth_service, _settings = _services(request)
    async with database.session() as session:
        user = await auth_service.authenticate(session, session_token)
    return UserResponse(id=user.user_id, login=user.login, role=user.role)


@router.delete("/session", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    database, auth_service, _settings = _services(request)
    async with database.session() as session:
        user = await auth_service.authenticate(session, session_token)
        await auth_service.revoke_session(
            session,
            user=user,
            csrf_token=csrf_token,
            request_id=request.state.request_id,
        )
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="strict")
