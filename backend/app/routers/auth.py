"""FR1 — registration, login, session inspection, logout.

Login mints a JWT whose `role` claim is the parent/teacher/administrator value, and records
a matching row in user_session so the token can be revoked. `get_current_user` re-checks
both on every request.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import AppUser, UserSession
from ..schemas import LoginRequest, RegisterRequest, SessionOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_session(db: DbSession, user: AppUser) -> SessionOut:
    token, jti, expires_at = create_access_token(
        user_id=user.id, email=user.email, role=user.role.value
    )
    db.add(UserSession(user_id=user.id, jti=jti, role=user.role, expires_at=expires_at))
    db.commit()
    return SessionOut(
        access_token=token,
        expires_at=expires_at,
        role=user.role,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> SessionOut:
    """Open registration for Phase 1 so the demo can be set up without seeding by hand.

    A real deployment would restrict this to administrators — see /users below.
    """
    existing = db.scalar(select(AppUser).where(AppUser.email == payload.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")

    user = AppUser(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _issue_session(db, user)


@router.post("/login", response_model=SessionOut)
def login(payload: LoginRequest, db: DbSession) -> SessionOut:
    user = db.scalar(select(AppUser).where(AppUser.email == payload.email))
    # Same message either way, so the endpoint does not confirm which emails exist.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")
    return _issue_session(db, user)


@router.get("/session", response_model=UserOut)
def current_session(user: CurrentUser) -> UserOut:
    """Used by the frontend on load to confirm a stored token is still valid."""
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, db: DbSession) -> None:
    """Revoke every live session for this user."""
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    ).all()
    for session in sessions:
        session.revoked_at = datetime.utcnow()
    db.commit()


@router.get("/users", response_model=list[UserOut])
def list_users(admin: AdminUser, db: DbSession) -> list[UserOut]:
    """Administrator-only, and the clearest demonstration that the role gate works."""
    users = db.scalars(select(AppUser).order_by(AppUser.id)).all()
    return [UserOut.model_validate(u) for u in users]
