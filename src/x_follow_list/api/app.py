import re
from collections.abc import Awaitable, Callable
from typing import Final
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from x_follow_list.api.auth import router as auth_router
from x_follow_list.api.binding import router as binding_router
from x_follow_list.application.auth import AuthService
from x_follow_list.application.binding import BrowserBindingService
from x_follow_list.application.errors import ApplicationError
from x_follow_list.config import Settings
from x_follow_list.observability.logging import configure_logging, log_context
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.readiness import database_is_ready
from x_follow_list.security.bootstrap import BootstrapTokenManager

SERVICE_NAME: Final = "x-follow-list-api"
SAFE_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9._-]{1,64}")


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    database = Database.from_settings(runtime_settings)
    bootstrap_tokens = BootstrapTokenManager(runtime_settings)
    if not bootstrap_tokens.is_consumed:
        bootstrap_tokens.get_or_create()
    auth_service = AuthService(runtime_settings, bootstrap_tokens)
    app = FastAPI(title="X Follow List API", version="0.1.0")
    app.state.settings = runtime_settings
    app.state.database = database
    app.state.auth_service = auth_service
    app.state.browser_binding_service = BrowserBindingService(database)

    @app.middleware("http")
    async def correlate_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id if SAFE_REQUEST_ID.fullmatch(supplied_request_id) else str(uuid4())
        )
        request.state.request_id = request_id
        with log_context(request_id=request_id):
            response: Response
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
                request.headers.get("Origin") != runtime_settings.app_origin
            ):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "code": "ORIGIN_REJECTED",
                        "message": "Request origin is not allowed",
                        "request_id": request_id,
                        "details": {},
                    },
                )
            else:
                response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ApplicationError)
    async def application_error_handler(
        request: Request, error: ApplicationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": error.code,
                "message": error.message,
                "request_id": request.state.request_id,
                "details": error.details,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        safe_errors = [
            {
                key: value
                for key, value in validation_error.items()
                if key in {"type", "loc", "msg"}
            }
            for validation_error in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "code": "REQUEST_VALIDATION_FAILED",
                "message": "Request validation failed",
                "request_id": request.state.request_id,
                "details": {"errors": safe_errors},
            },
        )

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"service": SERVICE_NAME, "status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness() -> JSONResponse:
        ready = await database_is_ready(database)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "service": SERVICE_NAME,
                "status": "ready" if ready else "not_ready",
                "database": "ready" if ready else "unavailable",
            },
        )

    app.include_router(auth_router)
    app.include_router(binding_router)

    return app


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    uvicorn.run(
        "x_follow_list.api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
    )
