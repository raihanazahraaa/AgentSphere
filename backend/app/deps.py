"""Shared FastAPI dependencies: DB session, the current user, and role gates.

Every protected route depends on `get_current_user`, so the role claim is validated on
each request rather than only at login.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import AppUser, UserRole, UserSession
from .security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> AppUser:
    if credentials is None:
        raise _unauthorised("Missing bearer token")

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _unauthorised("Invalid or expired token") from None

    session = db.scalar(select(UserSession).where(UserSession.jti == payload.get("jti", "")))
    if session is None:
        raise _unauthorised("Session not recognised")
    if session.revoked_at is not None:
        raise _unauthorised("Session has been signed out")
    if session.expires_at < datetime.utcnow():
        raise _unauthorised("Session has expired")

    user = db.get(AppUser, session.user_id)
    if user is None or not user.is_active:
        raise _unauthorised("User account is inactive")

    # A role changed after the token was minted must not keep the old privileges.
    if user.role != session.role:
        raise _unauthorised("Role has changed; please sign in again")

    return user


CurrentUser = Annotated[AppUser, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[AppUser], AppUser]:
    """Route guard: `Depends(require_roles(UserRole.administrator))`."""

    allowed = set(roles)

    def _guard(user: CurrentUser) -> AppUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(sorted(r.value for r in allowed))}",
            )
        return user

    return _guard


# Staff = anyone who may build or edit agents (FR3).
StaffUser = Annotated[AppUser, Depends(require_roles(UserRole.teacher, UserRole.administrator))]
AdminUser = Annotated[AppUser, Depends(require_roles(UserRole.administrator))]
