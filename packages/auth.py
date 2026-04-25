from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request


SESSION_TTL_DAYS = 14
PASSWORD_ITERATIONS = 210_000


@dataclass(frozen=True)
class AuthenticatedSession:
    user_id: str
    session_id: str
    email: str
    display_name: str = ""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw.encode("ascii"))
        expected = base64.b64decode(digest_raw.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def new_user_id() -> str:
    return f"user_{uuid.uuid4().hex}"


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def new_session_token() -> str:
    return secrets.token_urlsafe(36)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expires_at(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    return current + timedelta(days=SESSION_TTL_DAYS)


def require_authenticated_session(request: Request, repository) -> AuthenticatedSession:
    # Accept cookie (browser requests) or Bearer header (API clients)
    token = (
        request.cookies.get("spotiboys_token")
        or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    session = repository.get_auth_session(hash_session_token(token))
    if not session:
        raise HTTPException(status_code=401, detail="authentication required")
    return session
