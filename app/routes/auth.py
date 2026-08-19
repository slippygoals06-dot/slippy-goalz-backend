import re
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from supabase import create_client
from app.auth import (
    verify_password,
    hash_password,
    create_access_token,
    verify_token,
    require_owner,
    current_auth,
)
from app.config import (
    OWNER_USERNAME,
    OWNER_PASSWORD,
    STAFF_USERNAME,
    STAFF_PASSWORD,
    SUPABASE_URL,
    SUPABASE_KEY,
)
from app.staff import (
    ASSIGNABLE_PERMISSIONS,
    DEFAULT_STAFF_PERMISSIONS,
    SETUP_SQL,
    create_staff_row,
    get_staff_row,
    list_staff_rows,
    permissions_for_username,
    public_member,
    set_staff_active,
    set_staff_permissions,
    table_missing,
)
from app.rate_limit import SlidingWindowRateLimiter, client_ip
from app.errors import http_500

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Store hashed password (from env — unchanged)
HASHED_PASSWORD = hash_password(OWNER_PASSWORD)
HASHED_STAFF_PASSWORD = hash_password(STAFF_PASSWORD) if STAFF_USERNAME and STAFF_PASSWORD else None

PIN_RE = re.compile(r"^\d{4,6}$")
USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$")
MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 15 * 60

# In-memory rate limit for PIN / password unlock (per username)
_pin_attempts: dict = {}

# Login: 5 attempts per IP per 15 minutes (matches PIN lockout window)
_login_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=15 * 60)


class LoginRequest(BaseModel):
    username: str
    password: str


class PinSetRequest(BaseModel):
    pin: str
    password: str  # current owner password required to set/change


class PinClearRequest(BaseModel):
    password: str


class PinVerifyRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)


class UnlockPasswordRequest(BaseModel):
    password: str


class StaffCreateRequest(BaseModel):
    username: str
    password: str
    permissions: Optional[list] = None


class StaffActiveRequest(BaseModel):
    is_active: bool


class StaffPermissionsRequest(BaseModel):
    permissions: list


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_pin(pin: str) -> str:
    pin = (pin or "").strip()
    if not PIN_RE.match(pin):
        raise HTTPException(status_code=400, detail="PIN must be 4–6 digits")
    return pin


def _get_pin_hash(username: str) -> Optional[str]:
    try:
        res = (
            supabase.table("owner_settings")
            .select("pin_hash")
            .eq("username", username)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        return res.data[0].get("pin_hash") or None
    except Exception as e:
        raise http_500(e)


def _check_pin_rate(username: str) -> None:
    data = _pin_attempts.get(username) or {"count": 0, "lock_until": 0}
    now = time.time()
    if data.get("lock_until", 0) > now:
        mins = int((data["lock_until"] - now) / 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Locked for {mins} min.",
        )


def _record_pin_fail(username: str) -> dict:
    data = _pin_attempts.get(username) or {"count": 0, "lock_until": 0}
    count = int(data.get("count", 0)) + 1
    lock_until = time.time() + PIN_LOCKOUT_SECONDS if count >= MAX_PIN_ATTEMPTS else 0
    _pin_attempts[username] = {"count": count, "lock_until": lock_until}
    remaining = max(0, MAX_PIN_ATTEMPTS - count)
    return {"remaining": remaining, "locked": bool(lock_until)}


def _clear_pin_attempts(username: str) -> None:
    _pin_attempts.pop(username, None)


def _normalize_username(raw: str) -> str:
    return (raw or "").strip()


def _authenticate(username: str, password: str) -> Optional[str]:
    """Return role if credentials match, else None."""
    if not username or not password:
        return None
    if username == OWNER_USERNAME and verify_password(password, HASHED_PASSWORD):
        return "owner"
    if (
        STAFF_USERNAME
        and HASHED_STAFF_PASSWORD
        and username == STAFF_USERNAME
        and verify_password(password, HASHED_STAFF_PASSWORD)
    ):
        return "staff"
    row = get_staff_row(username)
    if (
        row
        and row.get("is_active")
        and row.get("password_hash")
        and verify_password(password, row["password_hash"])
    ):
        return "staff"
    return None


def _password_hash_for_user(username: str) -> Optional[str]:
    if username == OWNER_USERNAME:
        return HASHED_PASSWORD
    if STAFF_USERNAME and username == STAFF_USERNAME:
        return HASHED_STAFF_PASSWORD
    row = get_staff_row(username)
    if row and row.get("is_active"):
        return row.get("password_hash")
    return None


@router.post("/login")
def login(req: LoginRequest, request: Request):
    _login_limiter.check_or_raise(
        client_ip(request),
        detail="Too many attempts, try again later",
    )
    username = _normalize_username(req.username)
    role = _authenticate(username, req.password)
    if not role:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": username, "role": role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": username,
        "role": role,
        "permissions": permissions_for_username(username, role),
        "message": "Login successful",
    }


@router.get("/me")
def me(auth=Depends(current_auth)):
    return {
        "username": auth["username"],
        "role": auth["role"],
        "permissions": auth.get("permissions") or [],
    }


def _member_owner():
    return {
        "username": OWNER_USERNAME,
        "role": "owner",
        "is_active": True,
        "source": "env",
        "can_disable": False,
        "can_edit_permissions": False,
        "permissions": list(ASSIGNABLE_PERMISSIONS),
    }


def _member_env_staff():
    if not STAFF_USERNAME or not HASHED_STAFF_PASSWORD:
        return None
    return {
        "username": STAFF_USERNAME,
        "role": "staff",
        "is_active": True,
        "source": "env",
        "can_disable": False,
        "can_edit_permissions": False,
        "permissions": list(DEFAULT_STAFF_PERMISSIONS),
    }


@router.get("/staff")
def list_staff(user=Depends(require_owner)):
    members = [_member_owner()]
    env_staff = _member_env_staff()
    if env_staff:
        members.append(env_staff)
    try:
        for row in list_staff_rows():
            uname = row.get("username")
            if not uname or uname == OWNER_USERNAME or (STAFF_USERNAME and uname == STAFF_USERNAME):
                continue
            members.append(public_member(row, source="database", can_disable=True))
    except Exception as e:
        if not table_missing(e):
            raise http_500(e)
    return {"members": members}


@router.post("/staff")
def create_staff(req: StaffCreateRequest, user=Depends(require_owner)):
    username = _normalize_username(req.username)
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username must start with a letter and be 3–32 letters, numbers, or underscores.",
        )
    if username == OWNER_USERNAME or (STAFF_USERNAME and username == STAFF_USERNAME):
        raise HTTPException(status_code=400, detail="That username is already in use")
    password = req.password or ""
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if get_staff_row(username):
        raise HTTPException(status_code=400, detail="That username is already in use")
    try:
        member = create_staff_row(
            username,
            hash_password(password),
            permissions=req.permissions,
        )
        member["can_disable"] = True
        return member
    except Exception as e:
        if table_missing(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Staff accounts table is missing. Run migrations/014_staff_users.sql "
                    "in the Supabase SQL Editor, then try again."
                ),
            )
        raise http_500(e)


@router.post("/staff/{username}/active")
def set_staff_member_active(
    username: str,
    req: StaffActiveRequest,
    user=Depends(require_owner),
):
    username = _normalize_username(username)
    if username == OWNER_USERNAME or (STAFF_USERNAME and username == STAFF_USERNAME):
        raise HTTPException(status_code=400, detail="This account cannot be disabled here")
    try:
        row = set_staff_active(username, bool(req.is_active))
    except Exception as e:
        if table_missing(e):
            raise HTTPException(status_code=503, detail="Staff accounts table is missing")
        raise http_500(e)
    if not row:
        raise HTTPException(status_code=404, detail="Staff account not found")
    row["can_disable"] = True
    return row


@router.post("/staff/{username}/permissions")
def set_staff_member_permissions(
    username: str,
    req: StaffPermissionsRequest,
    user=Depends(require_owner),
):
    username = _normalize_username(username)
    if username == OWNER_USERNAME or (STAFF_USERNAME and username == STAFF_USERNAME):
        raise HTTPException(status_code=400, detail="This account's access cannot be changed here")
    try:
        row = set_staff_permissions(username, req.permissions)
    except Exception as e:
        if table_missing(e):
            raise HTTPException(status_code=503, detail="Staff accounts table is missing")
        raise HTTPException(
            status_code=503,
            detail="Run migrations/014_staff_users.sql in the Supabase SQL Editor so staff permissions can be saved.",
        )
    if not row:
        raise HTTPException(status_code=404, detail="Staff account not found")
    return row


@router.get("/staff/setup-sql")
def staff_setup_sql(user=Depends(require_owner)):
    return {
        "filename": "014_staff_users.sql",
        "sql": SETUP_SQL,
        "instructions": [
            "Open the Supabase SQL Editor",
            "Paste and run this SQL",
            "Return to Settings → Team and add a staff account",
        ],
    }


@router.get("/pin/status")
def pin_status(user=Depends(verify_token)):
    """Whether a Quick PIN is configured (for soft-lock vs hard-logout)."""
    try:
        pin_hash = _get_pin_hash(user)
        return {"pin_set": bool(pin_hash)}
    except HTTPException as e:
        # Missing table / Supabase blip should not break the whole session UI.
        if e.status_code == 500:
            return {"pin_set": False, "warning": e.detail}
        raise


@router.post("/pin/set")
def set_pin(req: PinSetRequest, user=Depends(require_owner)):
    """Set or replace Quick PIN. Requires current owner password."""
    if not verify_password(req.password, HASHED_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")
    pin = _validate_pin(req.pin)
    try:
        supabase.table("owner_settings").upsert({
            "username": user,
            "pin_hash": hash_password(pin),
            "updated_at": _now_iso(),
        }).execute()
        return {"ok": True, "pin_set": True}
    except Exception as e:
        raise http_500(e)


@router.post("/pin/clear")
def clear_pin(req: PinClearRequest, user=Depends(require_owner)):
    """Remove Quick PIN (reverts idle behavior to hard logout)."""
    if not verify_password(req.password, HASHED_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")
    try:
        supabase.table("owner_settings").upsert({
            "username": user,
            "pin_hash": None,
            "updated_at": _now_iso(),
        }).execute()
        return {"ok": True, "pin_set": False}
    except Exception as e:
        raise http_500(e)


@router.post("/pin/verify")
def verify_pin(req: PinVerifyRequest, user=Depends(verify_token)):
    """
    Unlock soft-lock UI. Session JWT stays valid — this only checks the PIN.
    Rate-limited: 5 fails → 15 min lockout.
    """
    _check_pin_rate(user)
    pin_hash = _get_pin_hash(user)
    if not pin_hash:
        raise HTTPException(status_code=400, detail="No PIN configured")

    pin = (req.pin or "").strip()
    if not PIN_RE.match(pin) or not verify_password(pin, pin_hash):
        result = _record_pin_fail(user)
        if result["locked"]:
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts. Locked for 15 minutes.",
            )
        raise HTTPException(
            status_code=401,
            detail=f"Incorrect PIN. {result['remaining']} attempt{'s' if result['remaining'] != 1 else ''} remaining.",
        )

    _clear_pin_attempts(user)
    return {"ok": True}


@router.post("/pin/unlock-password")
def unlock_with_password(req: UnlockPasswordRequest, user=Depends(verify_token)):
    """Fallback unlock with this account's password (does not issue a new JWT)."""
    _check_pin_rate(user)
    hashed = _password_hash_for_user(user)
    if not hashed or not verify_password(req.password, hashed):
        result = _record_pin_fail(user)
        if result["locked"]:
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts. Locked for 15 minutes.",
            )
        raise HTTPException(
            status_code=401,
            detail=f"Incorrect password. {result['remaining']} attempt{'s' if result['remaining'] != 1 else ''} remaining.",
        )
    _clear_pin_attempts(user)
    return {"ok": True}
