import re
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from supabase import create_client
from app.auth import verify_password, hash_password, create_access_token, verify_token
from app.config import OWNER_USERNAME, OWNER_PASSWORD, SUPABASE_URL, SUPABASE_KEY
from app.rate_limit import SlidingWindowRateLimiter, client_ip

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Store hashed password (from env — unchanged)
HASHED_PASSWORD = hash_password(OWNER_PASSWORD)

PIN_RE = re.compile(r"^\d{4,6}$")
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
        raise HTTPException(status_code=500, detail=str(e))


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


@router.post("/login")
def login(req: LoginRequest, request: Request):
    _login_limiter.check_or_raise(
        client_ip(request),
        detail="Too many attempts, try again later",
    )
    if req.username != OWNER_USERNAME:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, HASHED_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": req.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "message": "Login successful",
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
def set_pin(req: PinSetRequest, user=Depends(verify_token)):
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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pin/clear")
def clear_pin(req: PinClearRequest, user=Depends(verify_token)):
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
        raise HTTPException(status_code=500, detail=str(e))


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
    """Fallback unlock with owner password (does not issue a new JWT)."""
    _check_pin_rate(user)
    if not verify_password(req.password, HASHED_PASSWORD):
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
