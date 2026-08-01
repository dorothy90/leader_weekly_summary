from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext

router = APIRouter(prefix="/v1/mail-content", tags=["mail-content"])


class MailContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=128)


@router.post("/{content_id:path}", response_class=FileResponse)
async def get_mail_content(content_id: str, body: MailContentRequest, request: Request):
    try:
        policy = PolicyContext.from_user_id(body.user_id)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_USER_ID, "invalid owner") from exc
    store = request.app.state.container.mail_content
    content_path = store.resolve(content_id, policy) if store is not None else None
    if content_path is None:
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, "content unavailable")
    return FileResponse(
        content_path,
        media_type="text/html; charset=utf-8",
        headers={
            "content-security-policy": (
                "sandbox; default-src 'none'; img-src data:; "
                "style-src 'unsafe-inline'"
            ),
            "x-content-type-options": "nosniff",
        },
    )
