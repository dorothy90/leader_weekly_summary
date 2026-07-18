"""Authentication boundary for Knowledge API.

Header mode assumes a trusted reverse proxy strips inbound identity headers and
sets them after SSO authentication. Local demo mode stays dependency-free.
"""

from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict


EDITOR_ROLE = "knowledge-editor"


class UserContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    roles: frozenset[str]


def require_authenticated(
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> UserContext:
    mode = os.getenv("KNOWLEDGE_AUTH_MODE", "disabled").lower()
    if mode == "disabled":
        return UserContext(user_id="demo-user", roles=frozenset({EDITOR_ROLE}))
    if mode != "header":
        raise HTTPException(status_code=503, detail="Invalid KNOWLEDGE_AUTH_MODE")
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=401, detail="Authentication required")
    roles = frozenset(
        role.strip() for role in (x_user_roles or "").split(",") if role.strip()
    )
    return UserContext(user_id=x_user_id.strip(), roles=roles)


def require_editor(
    user: UserContext = Depends(require_authenticated),
) -> UserContext:
    if EDITOR_ROLE not in user.roles:
        raise HTTPException(status_code=403, detail="knowledge-editor role required")
    return user
