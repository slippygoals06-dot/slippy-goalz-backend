from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from app.config import SECRET_KEY, ALGORITHM, OWNER_USERNAME, STAFF_USERNAME
from app.staff import permissions_for_username, staff_is_active

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

ROLE_OWNER = "owner"
ROLE_STAFF = "staff"


def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def hash_password(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=8))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def parse_token(token: str) -> Optional[Tuple[str, str]]:
    """Return (username, role) if this is a valid dashboard JWT."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
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


def _user_from_token(token: str) -> Tuple[str, str]:
    parsed = parse_token(token)
    if not parsed:
        raise HTTPException(status_code=401, detail="Invalid token")
    return parsed


def verify_token(token: str = Depends(oauth2_scheme)):
    username, _role = _user_from_token(token)
    return username


def require_owner(token: str = Depends(oauth2_scheme)):
    username, role = _user_from_token(token)
    if role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Owner only")
    return username


def current_auth(token: str = Depends(oauth2_scheme)):
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
    """Any logged-in dashboard user (owner or staff). Used to allow staff to set booking price."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    parsed = parse_token(token)
    if not parsed:
        return None
    return parsed[0]
