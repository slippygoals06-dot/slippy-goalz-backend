"""JWT + cookie auth for Slippy Goalz dashboard."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import (
    ALGORITHM,
    IS_PRODUCTION,
    OWNER_USERNAME,
    SECRET_KEY,
    STAFF_USERNAME,
)
from app.sessions import is_refresh_valid
from app.staff import permissions_for_username, staff_is_active

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

ROLE_OWNER = "owner"
ROLE_STAFF = "staff"

ACCESS_TOKEN_MINUTES = 30
REFRESH_TOKEN_DAYS = 7

COOKIE_ACCESS = "slippy_access"
COOKIE_REFRESH = "slippy_refresh"


def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)


def hash_password(password):
    return pwd_context.hash(password)


def _cookie_common() -> dict:
    return {
        "httponly": True,
        "secure": IS_PRODUCTION,
        "samesite": "none" if IS_PRODUCTION else "lax",
        "path": "/",
    }


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    common = _cookie_common()
    response.set_cookie(
        COOKIE_ACCESS,
        access,
        max_age=ACCESS_TOKEN_MINUTES * 60,
        **common,
    )
    response.set_cookie(
        COOKIE_REFRESH,
        refresh,
        max_age=REFRESH_TOKEN_DAYS * 24 * 3600,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    common = _cookie_common()
    response.delete_cookie(COOKIE_ACCESS, path="/")
    response.delete_cookie(COOKIE_REFRESH, path="/")
    # Also clear with secure variants browsers may have set
    for name in (COOKIE_ACCESS, COOKIE_REFRESH):
        response.set_cookie(
            name,
            "",
            max_age=0,
            httponly=True,
            secure=IS_PRODUCTION,
            samesite=common["samesite"],
            path="/",
        )


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    *,
    jti: Optional[str] = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_MINUTES)
    )
    to_encode.update(
        {
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
            "jti": jti or uuid.uuid4().hex,
        }
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(
    username: str,
    role: str,
    *,
    jti: Optional[str] = None,
) -> Tuple[str, str, datetime]:
    refresh_jti = jti or uuid.uuid4().hex
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS)
    payload = {
        "sub": username,
        "role": role,
        "type": "refresh",
        "jti": refresh_jti,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, refresh_jti, expire


def decode_token(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def parse_token(token: str) -> Optional[Tuple[str, str]]:
    """Return (username, role) if this is a valid dashboard access JWT."""
    payload = decode_token(token)
    if not payload:
        return None
    if payload.get("pre_auth"):
        return None
    if payload.get("type") not in (None, "access"):
        if payload.get("type") == "refresh":
            return None
    username = payload.get("sub")
    if not username:
        return None
    role = payload.get("role")
    if role not in (ROLE_OWNER, ROLE_STAFF):
        if username == OWNER_USERNAME:
            role = ROLE_OWNER
        else:
            return None
    if role == ROLE_OWNER:
        if username != OWNER_USERNAME:
            return None
        return username, ROLE_OWNER
    if STAFF_USERNAME and username == STAFF_USERNAME:
        return username, ROLE_STAFF
    if staff_is_active(username):
        return username, ROLE_STAFF
    return None


def parse_refresh_token(token: str) -> Optional[Tuple[str, str, str]]:
    """Return (username, role, jti) if refresh JWT is valid and not revoked."""
    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        return None
    jti = payload.get("jti")
    username = payload.get("sub")
    role = payload.get("role")
    if not jti or not username or role not in (ROLE_OWNER, ROLE_STAFF):
        return None
    if not is_refresh_valid(jti):
        return None
    if role == ROLE_OWNER and username != OWNER_USERNAME:
        return None
    if role == ROLE_STAFF:
        if STAFF_USERNAME and username == STAFF_USERNAME:
            return username, role, jti
        if not staff_is_active(username):
            return None
    return username, role, jti


def extract_access_token(
    request: Request,
    bearer: Optional[str] = Depends(oauth2_scheme),
) -> str:
    cookie = request.cookies.get(COOKIE_ACCESS)
    if cookie:
        return cookie
    if bearer:
        return bearer
    raise HTTPException(status_code=401, detail="Not authenticated")


def _user_from_token(token: str) -> Tuple[str, str]:
    parsed = parse_token(token)
    if not parsed:
        raise HTTPException(status_code=401, detail="Invalid token")
    return parsed


def verify_token(token: str = Depends(extract_access_token)):
    username, _role = _user_from_token(token)
    return username


def require_owner(token: str = Depends(extract_access_token)):
    username, role = _user_from_token(token)
    if role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Owner only")
    return username


def current_auth(token: str = Depends(extract_access_token)):
    username, role = _user_from_token(token)
    return {
        "username": username,
        "role": role,
        "permissions": permissions_for_username(username, role),
    }


def require_perm(perm: str):
    def _dep(auth: dict = Depends(current_auth)):
        if auth["role"] == ROLE_OWNER:
            return auth["username"]
        if perm not in (auth.get("permissions") or []):
            raise HTTPException(status_code=403, detail="Not allowed")
        return auth["username"]

    return _dep


def optional_owner(request: Request) -> Optional[str]:
    """Any logged-in dashboard user (cookie or Bearer)."""
    token = request.cookies.get(COOKIE_ACCESS)
    if not token:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    parsed = parse_token(token)
    if not parsed:
        return None
    return parsed[0]


def create_pre_auth_token(username: str, role: str) -> str:
    """Short-lived token for 2FA second step (not a session)."""
    return create_access_token(
        {"sub": username, "role": role, "pre_auth": True},
        expires_delta=timedelta(minutes=5),
        jti=uuid.uuid4().hex,
    )


def parse_pre_auth(token: str) -> Optional[Tuple[str, str]]:
    payload = decode_token(token)
    if not payload or not payload.get("pre_auth"):
        return None
    username = payload.get("sub")
    role = payload.get("role")
    if not username or role not in (ROLE_OWNER, ROLE_STAFF):
        return None
    if role == ROLE_OWNER and username != OWNER_USERNAME:
        return None
    return username, role
