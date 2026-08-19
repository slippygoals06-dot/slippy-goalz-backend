"""Integrations routes — WhatsApp / Instagram / TikTok connect + status (JWT-protected)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import create_client

from app.auth import require_owner
from app.config import SUPABASE_URL, SUPABASE_KEY
from app.errors import http_500

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GRAPH_API_VERSION = "v22.0"
WHATSAPP_CHANNEL = "whatsapp"
SOCIAL_CHANNELS = ("instagram", "tiktok")
LISTED_CHANNELS = ("whatsapp", "instagram", "tiktok")
INTEGRATION_SELECT = (
    "channel, phone_number_id, waba_id, status, "
    "last_verified_at, created_at, updated_at"
)


class WhatsAppConnectRequest(BaseModel):
    phone_number_id: str = Field(..., min_length=1)
    access_token: str = Field(..., min_length=1)


class InstagramConnectRequest(BaseModel):
    username: str = Field(..., min_length=1)
    account_id: str = ""
    access_token: str = ""


class TikTokConnectRequest(BaseModel):
    username: str = Field(..., min_length=1)
    access_token: str = ""
    client_key: str = ""


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


def _normalize_username(raw: str) -> str:
    return (raw or "").strip().lstrip("@")


def _verify_instagram_credentials(
    account_id: str, access_token: str
) -> Dict[str, Any]:
    """Confirm Instagram account id + token against Meta Graph. Raises HTTPException."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{account_id}"
    params = {"fields": "id,username,name"}
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


def _fetch_channel(channel: str, include_token: bool = False) -> Optional[Dict[str, Any]]:
    cols = INTEGRATION_SELECT
    if include_token:
        cols = f"{INTEGRATION_SELECT}, access_token"
    try:
        res = (
            supabase.table("integrations")
            .select(cols)
            .eq("channel", channel)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise http_500(e)
    rows = res.data or []
    return rows[0] if rows else None


def _upsert_channel(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("integrations")
            .upsert(row, on_conflict="channel")
            .execute()
        )
    except Exception as e:
        raise http_500(e)
    return res.data[0] if res.data else None


def _public_integration_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return integration fields safe for the dashboard (omit token)."""
    status = row.get("status") or "not_connected"
    username = row.get("waba_id") or ""
    account_id = row.get("phone_number_id") or ""
    return {
        "connected": status == "connected",
        "channel": row.get("channel"),
        "username": username,
        "account_id": account_id,
        "phone_number_id": account_id,
        "waba_id": username,
        "status": status,
        "last_verified_at": row.get("last_verified_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _empty_channel(channel: str) -> Dict[str, Any]:
    return {
        "connected": False,
        "channel": channel,
        "username": "",
        "account_id": "",
        "status": "not_connected",
        "last_verified_at": None,
    }


@router.post("/whatsapp/connect")
def connect_whatsapp(body: WhatsAppConnectRequest, user=Depends(require_owner)):
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

    saved = _upsert_channel(row)
    return {
        "ok": True,
        "status": "connected",
        "display_phone_number": meta.get("display_phone_number"),
        "verified_name": meta.get("verified_name"),
        "phone_number_id": phone_number_id,
        "integration": _public_integration_row(saved) if saved else None,
    }


@router.get("/whatsapp/status")
def whatsapp_status(user=Depends(require_owner)):
    row = _fetch_channel(WHATSAPP_CHANNEL)
    if not row:
        return {"connected": False, "status": "not_connected"}
    return _public_integration_row(row)


@router.get("/")
def list_integrations(user=Depends(require_owner)):
    try:
        res = (
            supabase.table("integrations")
            .select(INTEGRATION_SELECT)
            .execute()
        )
    except Exception as e:
        raise http_500(e)

    by_channel = {
        row.get("channel"): _public_integration_row(row)
        for row in (res.data or [])
        if row.get("channel")
    }
    return {ch: by_channel.get(ch) or _empty_channel(ch) for ch in LISTED_CHANNELS}


@router.post("/instagram/connect")
def connect_instagram(body: InstagramConnectRequest, user=Depends(require_owner)):
    username = _normalize_username(body.username)
    account_id = (body.account_id or "").strip()
    access_token = (body.access_token or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Instagram username is required")

    existing = _fetch_channel("instagram", include_token=True)
    if not access_token:
        access_token = (existing or {}).get("access_token") or ""
    if not account_id:
        account_id = (existing or {}).get("phone_number_id") or ""

    verified_username = None
    if access_token and account_id:
        meta = _verify_instagram_credentials(account_id, access_token)
        verified_username = (meta.get("username") or "").strip().lstrip("@")
        if verified_username:
            username = verified_username

    now = _now_iso()
    saved = _upsert_channel(
        {
            "channel": "instagram",
            "phone_number_id": account_id or None,
            "waba_id": username,
            "access_token": access_token or None,
            "status": "connected",
            "last_verified_at": now,
            "updated_at": now,
        }
    )
    return {
        "ok": True,
        "status": "connected",
        "username": username,
        "account_id": account_id,
        "verified": bool(access_token and account_id),
        "integration": _public_integration_row(saved) if saved else None,
    }


@router.post("/tiktok/connect")
def connect_tiktok(body: TikTokConnectRequest, user=Depends(require_owner)):
    username = _normalize_username(body.username)
    access_token = (body.access_token or "").strip()
    client_key = (body.client_key or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="TikTok username is required")

    existing = _fetch_channel("tiktok", include_token=True)
    if not access_token:
        access_token = (existing or {}).get("access_token") or ""
    if not client_key:
        client_key = (existing or {}).get("phone_number_id") or ""

    now = _now_iso()
    saved = _upsert_channel(
        {
            "channel": "tiktok",
            "phone_number_id": client_key or None,
            "waba_id": username,
            "access_token": access_token or None,
            "status": "connected",
            "last_verified_at": now,
            "updated_at": now,
        }
    )
    return {
        "ok": True,
        "status": "connected",
        "username": username,
        "integration": _public_integration_row(saved) if saved else None,
    }


@router.get("/{channel}/status")
def channel_status(channel: str, user=Depends(require_owner)):
    channel = (channel or "").strip().lower()
    if channel not in SOCIAL_CHANNELS:
        raise HTTPException(status_code=404, detail="Unknown integration")
    row = _fetch_channel(channel)
    if not row:
        return _empty_channel(channel)
    return _public_integration_row(row)


@router.post("/{channel}/disconnect")
def disconnect_channel(channel: str, user=Depends(require_owner)):
    channel = (channel or "").strip().lower()
    if channel not in SOCIAL_CHANNELS:
        raise HTTPException(status_code=400, detail="Cannot disconnect this channel")

    now = _now_iso()
    saved = _upsert_channel(
        {
            "channel": channel,
            "phone_number_id": None,
            "waba_id": None,
            "access_token": None,
            "status": "not_connected",
            "last_verified_at": None,
            "updated_at": now,
        }
    )
    return {
        "ok": True,
        "status": "not_connected",
        "integration": _public_integration_row(saved) if saved else _empty_channel(channel),
    }
