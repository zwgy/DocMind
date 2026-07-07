from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db
from yuxi.services.external_user_token_service import exchange_external_user_iframe_token
from yuxi.services.operation_log_service import log_operation

external_user_iframe_tokens = APIRouter(prefix="/chat-iframe", tags=["chat-iframe"])


class IframeExternalUserTokenRequest(BaseModel):
    source_system: str
    external_user_id: str
    external_user_name: str


@external_user_iframe_tokens.post("/token")
async def exchange_iframe_external_user_token(
    payload: IframeExternalUserTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await exchange_external_user_iframe_token(
        db,
        source_system=payload.source_system,
        external_user_id=payload.external_user_id,
        external_user_name=payload.external_user_name,
        origin=request.headers.get("origin"),
    )
    await log_operation(
        db,
        result["user_id"],
        "chat-iframe 自助换票",
        f"source_system={result['source_system']}, uid={result['uid']}",
        request,
    )
    return result
