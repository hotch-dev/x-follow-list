from __future__ import annotations

from fastapi import Request

from x_follow_list.application.auth import AuthenticatedUser, AuthService
from x_follow_list.persistence.database import Database


async def authenticate_request(
    request: Request,
    session_token: str | None,
    csrf_token: str | None = None,
    *,
    mutation: bool = False,
) -> AuthenticatedUser:
    database: Database = request.app.state.database
    auth_service: AuthService = request.app.state.auth_service
    async with database.session() as session:
        user = await auth_service.authenticate(session, session_token)
    if mutation:
        auth_service.verify_csrf(user, csrf_token)
    return user
