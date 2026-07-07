from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_superadmin_user
from yuxi.services.external_user_token_service import exchange_external_user_backend_token
from yuxi.services.operation_log_service import log_operation
from yuxi.storage.postgres.models_business import User

external_user_backend_tokens = APIRouter(prefix="/external-users", tags=["external-users"])


class ExternalUserTokenRequest(BaseModel):
    source_system: str
    external_user_id: str
    external_user_name: str


@external_user_backend_tokens.post("/token")
async def exchange_backend_external_user_token(
    payload: ExternalUserTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    result = await exchange_external_user_backend_token(
        db,
        source_system=payload.source_system,
        external_user_id=payload.external_user_id,
        external_user_name=payload.external_user_name,
    )
    await log_operation(
        db,
        current_user.id,
        "外部用户后端换票",
        f"source_system={result['source_system']}, uid={result['uid']}",
        request,
    )
    return result
