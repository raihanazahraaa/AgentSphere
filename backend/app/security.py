"""Password hashing and session-token issuing for FR1.

Hashing uses PBKDF2-HMAC-SHA256 from the standard library so the project has no native
build dependency. The two functions below are the only place hashing happens — swap them
for argon2/bcrypt before any real deployment without touching the routers.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings

_ITERATIONS = 390_000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(digest, expected)


def create_access_token(*, user_id: int, email: str, role: str) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at).

    The role claim is what every downstream request is authorised against, and what gets
    handed to the agent runtime as its RoleContext.
    """
    settings = get_settings()
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at.replace(tzinfo=None)


def decode_access_token(token: str) -> dict:
    """Raise jwt.PyJWTError if the token is invalid or expired."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
