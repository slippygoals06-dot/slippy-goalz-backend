"""Server-side auth session registry (refresh jti revoke)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from supabase import create_client

from app.config import SUPABASE_URL, SUPABASE_KEY

_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def create_session(
    *,
    username: str,
    refresh_jti: str,
    access_jti: str,
    expires_at: datetime,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    try:
        _supabase.table("auth_sessions").insert(
            {
                "username": username,
                "refresh_jti": refresh_jti,
                "access_jti": access_jti,
                "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
                "ip": (ip or "")[:80] or None,
                "user_agent": (user_agent or "")[:240] or None,
            }
        ).execute()
    except Exception as e:
        print(f"auth_sessions insert failed: {e}")


def session_by_refresh_jti(jti: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _supabase.table("auth_sessions")
            .select("*")
            .eq("refresh_jti", jti)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        return None


def is_refresh_valid(jti: str) -> bool:
    row = session_by_refresh_jti(jti)
    if not row:
        return False
    if row.get("revoked_at"):
        return False
    exp = row.get("expires_at")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt < datetime.now(timezone.utc):
                return False
        except Exception:
            pass
    return True


def revoke_refresh_jti(jti: str) -> None:
    try:
        _supabase.table("auth_sessions").update(
            {"revoked_at": datetime.now(timezone.utc).isoformat()}
        ).eq("refresh_jti", jti).is_("revoked_at", "null").execute()
    except Exception as e:
        print(f"revoke refresh failed: {e}")


def revoke_username_sessions(username: str) -> int:
    try:
        res = (
            _supabase.table("auth_sessions")
            .update({"revoked_at": datetime.now(timezone.utc).isoformat()})
            .eq("username", username)
            .is_("revoked_at", "null")
            .execute()
        )
        return len(res.data or [])
    except Exception as e:
        print(f"revoke username sessions failed: {e}")
        return 0


def rotate_access_jti(refresh_jti: str, access_jti: str) -> None:
    try:
        _supabase.table("auth_sessions").update({"access_jti": access_jti}).eq(
            "refresh_jti", refresh_jti
        ).is_("revoked_at", "null").execute()
    except Exception as e:
        print(f"rotate access jti failed: {e}")
