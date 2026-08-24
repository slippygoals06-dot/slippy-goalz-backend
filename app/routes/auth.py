import re
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from pydantic import BaseModel, Field
from typing import Optional
from supabase import create_client
from app.auth import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    create_pre_auth_token,
    parse_pre_auth,
    parse_refresh_token,
    set_auth_cookies,
    clear_auth_cookies,
    COOKIE_REFRESH,
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
from app.sessions import create_session, revoke_refresh_jti, revoke_username_sessions, rotate_access_jti
from app.crypto_secrets import encrypt_secret, decrypt_secret
from app.audit import log_audit_event
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
import uuid

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


class Login2FARequest(BaseModel):
    pre_auth: str = Field(..., min_length=10)
    code: str = Field(..., min_length=6, max_length=8)


class TotpEnableRequest(BaseModel):
    password: str
    code: str = Field(..., min_length=6, max_length=8)


class TotpDisableRequest(BaseModel):
    password: str
    code: str = Field(..., min_length=6, max_length=8)


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


def _owner_totp_enabled() -> bool:
    try:
        res = (
            supabase.table("owner_settings")
            .select("totp_enabled")
            .eq("username", OWNER_USERNAME)
            .limit(1)
            .execute()
        )
        if not res.data:
            return False
        return bool(res.data[0].get("totp_enabled"))
    except Exception:
        return False


def _owner_totp_secret() -> Optional[str]:
    try:
        res = (
            supabase.table("owner_settings")
            .select("totp_secret, totp_enabled")
            .eq("username", OWNER_USERNAME)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]
        if not row.get("totp_enabled"):
            return None
        return decrypt_secret(row.get("totp_secret"))
    except Exception:
        return None


def _verify_totp(secret: str, code: str) -> bool:
    try:
        import pyotp
    except ImportError:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(str(code).strip(), valid_window=1))


def _issue_session_response(
    *,
    response: Response,
    request: Request,
    username: str,
    role: str,
) -> dict:
    access_jti = uuid.uuid4().hex
    access = create_access_token({"sub": username, "role": role}, jti=access_jti)
    refresh, refresh_jti, exp = create_refresh_token(username, role)
    create_session(
        username=username,
        refresh_jti=refresh_jti,
        access_jti=access_jti,
        expires_at=exp,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_auth_cookies(response, access, refresh)
    log_audit_event(
        actor=username,
        action="login_ok",
        details={"role": role, "ip": client_ip(request)},
    )
    from app.config import IS_PRODUCTION

    body = {
        "ok": True,
        "token_type": "bearer",
        "username": username,
        "role": role,
        "permissions": permissions_for_username(username, role),
        "message": "Login successful",
        "cookie_auth": True,
    }
    # Local/dev: also return token for Bearer fallback (cross-port localhost cookies are flaky)
    if not IS_PRODUCTION:
        body["access_token"] = access
    return body


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    _login_limiter.check_or_raise(
        client_ip(request),
        detail="Too many attempts, try again later",
    )
    username = _normalize_username(req.username)
    role = _authenticate(username, req.password)
    if not role:
        log_audit_event(
            actor=username or "unknown",
            action="login_fail",
            details={"ip": client_ip(request)},
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if role == "owner" and _owner_totp_enabled():
        pre = create_pre_auth_token(username, role)
        return {
            "ok": True,
            "requires_2fa": True,
            "pre_auth": pre,
            "username": username,
            "role": role,
            "message": "Enter your authenticator code",
        }

    return _issue_session_response(
        response=response, request=request, username=username, role=role
    )


@router.post("/login/2fa")
def login_2fa(req: Login2FARequest, request: Request, response: Response):
    _login_limiter.check_or_raise(
        client_ip(request),
        detail="Too many attempts, try again later",
    )
    parsed = parse_pre_auth(req.pre_auth)
    if not parsed:
        raise HTTPException(status_code=401, detail="2FA session expired — sign in again")
    username, role = parsed
    if role != "owner":
        raise HTTPException(status_code=403, detail="2FA is owner-only")
    secret = _owner_totp_secret()
    if not secret or not _verify_totp(secret, req.code):
        log_audit_event(
            actor=username,
            action="login_fail",
            details={"ip": client_ip(request), "reason": "bad_totp"},
        )
        raise HTTPException(status_code=401, detail="Invalid authenticator code")
    return _issue_session_response(
        response=response, request=request, username=username, role=role
    )


@router.post("/refresh")
def refresh_session(request: Request, response: Response):
    raw = request.cookies.get(COOKIE_REFRESH)
    if not raw:
        raise HTTPException(status_code=401, detail="No refresh session")
    parsed = parse_refresh_token(raw)
    if not parsed:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh expired — sign in again")
    username, role, refresh_jti = parsed
    access_jti = uuid.uuid4().hex
    access = create_access_token({"sub": username, "role": role}, jti=access_jti)
    rotate_access_jti(refresh_jti, access_jti)
    set_auth_cookies(response, access, raw)
    return {
        "ok": True,
        "username": username,
        "role": role,
        "permissions": permissions_for_username(username, role),
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    actor = "unknown"
    raw = request.cookies.get(COOKIE_REFRESH)
    if raw:
        parsed = parse_refresh_token(raw)
        if parsed:
            actor = parsed[0]
            revoke_refresh_jti(parsed[2])
        else:
            from app.auth import decode_token

            payload = decode_token(raw)
            if payload and payload.get("jti"):
                actor = payload.get("sub") or actor
                revoke_refresh_jti(payload["jti"])
    clear_auth_cookies(response)
    log_audit_event(actor=actor, action="logout", details={"ip": client_ip(request)})
    return {"ok": True}


@router.get("/2fa/status")
def totp_status(user=Depends(require_owner)):
    return {"enabled": _owner_totp_enabled()}


@router.post("/2fa/setup")
def totp_setup(user=Depends(require_owner)):
    """Generate a new TOTP secret (not enabled until /2fa/enable)."""
    try:
        import pyotp
    except ImportError as e:
        raise HTTPException(status_code=503, detail="2FA library missing on server") from e

    secret = pyotp.random_base32()
    enc = encrypt_secret(secret)
    try:
        existing = (
            supabase.table("owner_settings")
            .select("pin_hash")
            .eq("username", user)
            .limit(1)
            .execute()
        )
        pin_hash = existing.data[0].get("pin_hash") if existing.data else None
        supabase.table("owner_settings").upsert(
            {
                "username": user,
                "pin_hash": pin_hash,
                "totp_secret": enc,
                "totp_enabled": False,
                "updated_at": _now_iso(),
            }
        ).execute()
    except Exception as e:
        raise http_500(e)

    uri = pyotp.TOTP(secret).provisioning_uri(name=user, issuer_name="Slippy Goalz Arena")
    return {"ok": True, "secret": secret, "otpauth_url": uri}


@router.post("/2fa/enable")
def totp_enable(req: TotpEnableRequest, user=Depends(require_owner)):
    if not verify_password(req.password, HASHED_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")
    try:
        res = (
            supabase.table("owner_settings")
            .select("totp_secret")
            .eq("username", user)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise http_500(e)
    if not res.data or not res.data[0].get("totp_secret"):
        raise HTTPException(status_code=400, detail="Call /auth/2fa/setup first")
    secret = decrypt_secret(res.data[0].get("totp_secret"))
    if not secret or not _verify_totp(secret, req.code):
        raise HTTPException(status_code=401, detail="Invalid authenticator code")
    try:
        supabase.table("owner_settings").update(
            {"totp_enabled": True, "updated_at": _now_iso()}
        ).eq("username", user).execute()
    except Exception as e:
        raise http_500(e)
    log_audit_event(actor=user, action="totp_enabled", details={})
    return {"ok": True, "enabled": True}


@router.post("/2fa/disable")
def totp_disable(req: TotpDisableRequest, user=Depends(require_owner)):
    if not verify_password(req.password, HASHED_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")
    secret = _owner_totp_secret()
    if secret and not _verify_totp(secret, req.code):
        # If already disabled, allow password-only
        if _owner_totp_enabled():
            raise HTTPException(status_code=401, detail="Invalid authenticator code")
    try:
        supabase.table("owner_settings").update(
            {
                "totp_enabled": False,
                "totp_secret": None,
                "updated_at": _now_iso(),
            }
        ).eq("username", user).execute()
    except Exception as e:
        raise http_500(e)
    log_audit_event(actor=user, action="totp_disabled", details={})
    return {"ok": True, "enabled": False}


@router.get("/me")
def me(auth=Depends(current_auth)):
    return {
        "username": auth["username"],
        "role": auth["role"],
        "permissions": auth.get("permissions") or [],
        "totp_enabled": _owner_totp_enabled() if auth["role"] == "owner" else False,
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
    if not req.is_active:
        n = revoke_username_sessions(username)
        log_audit_event(
            actor=user,
            action="session_revoked",
            details={"target": username, "sessions": n},
        )
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
