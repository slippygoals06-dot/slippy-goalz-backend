"""Integrations routes — WhatsApp Cloud API connect / status (JWT-protected)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import create_client

from app.auth import verify_token
from app.config import SUPABASE_URL, SUPABASE_KEY

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GRAPH_API_VERSION = "v22.0"
WHATSAPP_CHANNEL = "whatsapp"


class WhatsAppConnectRequest(BaseModel):
    phone_number_id: str = Field(..., min_length=1)
    access_token: str = Field(..., min_length=1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_error_message(body: Any, status_code: int) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if err:
            return str(err)
    return f"Meta Graph API error ({status_code})"


def _verify_whatsapp_credentials(
    phone_number_id: str, access_token: str
) -> Dict[str, Any]:
    """Call Meta to confirm phone_number_id + token. Raises HTTPException on failure."""
    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{phone_number_id}"
    )
    params = {"fields": "display_phone_number,verified_name"}
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.get(url, headers=headers, params=params)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Meta Graph API: {e}",
        )

    try:
        body = res.json()
    except Exception:
        body = {"raw": res.text}

    if not res.is_success:
        raise HTTPException(
            status_code=400,
            detail=_meta_error_message(body, res.status_code),
        )

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502,
            detail="Unexpected response from Meta Graph API",
        )

    return body


def _public_integration_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return integration fields safe for the dashboard (omit token)."""
    return {
        "connected": True,
        "channel": row.get("channel"),
        "phone_number_id": row.get("phone_number_id"),
        "waba_id": row.get("waba_id"),
        "status": row.get("status"),
        "last_verified_at": row.get("last_verified_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.post("/whatsapp/connect")
def connect_whatsapp(body: WhatsAppConnectRequest, user=Depends(verify_token)):
    phone_number_id = body.phone_number_id.strip()
    access_token = body.access_token.strip()
    if not phone_number_id or not access_token:
        raise HTTPException(
            status_code=400,
            detail="phone_number_id and access_token are required",
        )

    meta = _verify_whatsapp_credentials(phone_number_id, access_token)
    now = _now_iso()

    # TODO: encrypt access_token before production
    row = {
        "channel": WHATSAPP_CHANNEL,
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "status": "connected",
        "last_verified_at": now,
        "updated_at": now,
    }

    try:
        res = (
            supabase.table("integrations")
            .upsert(row, on_conflict="channel")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    saved: Optional[Dict[str, Any]] = res.data[0] if res.data else None
    return {
        "ok": True,
        "status": "connected",
        "display_phone_number": meta.get("display_phone_number"),
        "verified_name": meta.get("verified_name"),
        "phone_number_id": phone_number_id,
        "integration": _public_integration_row(saved) if saved else None,
    }


@router.get("/whatsapp/status")
def whatsapp_status(user=Depends(verify_token)):
    try:
        res = (
            supabase.table("integrations")
            .select(
                "channel, phone_number_id, waba_id, status, "
                "last_verified_at, created_at, updated_at"
            )
            .eq("channel", WHATSAPP_CHANNEL)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    rows = res.data or []
    if not rows:
        return {"connected": False, "status": "not_connected"}

    return _public_integration_row(rows[0])
