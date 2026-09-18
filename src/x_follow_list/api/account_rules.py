from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Cookie, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from x_follow_list.api.dependencies import authenticate_request
from x_follow_list.application.account_rules import AccountRuleService
from x_follow_list.application.auth import SESSION_COOKIE_NAME

router = APIRouter(prefix="/api/v1/account-rules", tags=["account-rules"])


class CreateAccountRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_account_id: str = Field(min_length=1, max_length=255)
    subject_x_user_id: str = Field(min_length=1, max_length=64)
    rule_type: Literal["ALLOWLIST", "BUSINESS_BLOCKLIST"]
    reason: str = Field(min_length=1, max_length=500)


class DeleteAccountRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class AccountRuleResponse(BaseModel):
    id: str
    x_account_id: str
    subject_x_user_id: str
    rule_type: str
    reason: str
    version: int
    created_at: datetime
    updated_at: datetime
    scan_required: bool = False


class AccountRuleListResponse(BaseModel):
    items: list[AccountRuleResponse]
    next_cursor: str | None = None


def _service(request: Request) -> AccountRuleService:
    return cast(AccountRuleService, request.app.state.account_rule_service)


@router.post("", status_code=201, response_model=AccountRuleResponse)
async def create_rule(
    payload: CreateAccountRuleRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AccountRuleResponse:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    rule = await _service(request).create(
        user.user_id,
        account_id=payload.x_account_id,
        subject_x_user_id=payload.subject_x_user_id,
        rule_type=payload.rule_type,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    return AccountRuleResponse(**rule)


@router.get("", response_model=AccountRuleListResponse)
async def list_rules(
    request: Request,
    x_account_id: Annotated[str, Query(min_length=1, max_length=255)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AccountRuleListResponse:
    user = await authenticate_request(request, session_token)
    rows, next_cursor = await _service(request).list_rules(
        user.user_id, x_account_id, limit, cursor
    )
    return AccountRuleListResponse(
        items=[AccountRuleResponse(**row) for row in rows], next_cursor=next_cursor
    )


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: str,
    payload: DeleteAccountRuleRequest,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    user = await authenticate_request(request, session_token, csrf_token, mutation=True)
    await _service(request).delete(user.user_id, rule_id, payload.version, request.state.request_id)
    return Response(status_code=204)
