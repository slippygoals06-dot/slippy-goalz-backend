"""Booking attachments — service-role only. No anon browser uploads."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from supabase import create_client

from app.auth import optional_owner, require_perm
from app.config import SUPABASE_URL, SUPABASE_KEY
from app.errors import http_500
from app.rate_limit import SlidingWindowRateLimiter, client_ip

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_MIMES = {"video/mp4", "video/quicktime"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_IMAGES = 5
MAX_VIDEOS = 1
SIGNED_URL_SECONDS = 3600

_upload_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60, prefix="attach")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _resolve_booking(booking_ref: str) -> Dict[str, Any]:
    ref = (booking_ref or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="booking id required")
    try:
        if _UUID_RE.match(ref):
            res = (
                supabase.table("bookings")
                .select('id, "Booking ID", Status, created_at')
                .eq("id", ref)
                .limit(1)
                .execute()
            )
        else:
            res = (
                supabase.table("bookings")
                .select('id, "Booking ID", Status, created_at')
                .eq("Booking ID", ref)
                .limit(1)
                .execute()
            )
    except Exception as e:
        raise http_500(e)
    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found")
    return res.data[0]


def _bucket_for(file_type: str) -> str:
    return "booking-videos" if file_type == "video" else "booking-images"


def _count_attachments(booking_uuid: str, file_type: str) -> int:
    res = (
        supabase.table("booking_attachments")
        .select("id", count="exact")
        .eq("booking_id", booking_uuid)
        .eq("file_type", file_type)
        .execute()
    )
    if res.count is not None:
        return int(res.count)
    return len(res.data or [])


def _signed_url(bucket: str, path: str) -> Optional[str]:
    try:
        res = supabase.storage.from_(bucket).create_signed_url(path, SIGNED_URL_SECONDS)
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        # storage3 may return object with signed_url attr
        return getattr(res, "signed_url", None) or getattr(res, "signedURL", None)
    except Exception:
        return None


@router.get("/{booking_ref}/attachments")
def list_attachments(booking_ref: str, user=Depends(require_perm("bookings"))):
    booking = _resolve_booking(booking_ref)
    booking_uuid = booking["id"]
    try:
        res = (
            supabase.table("booking_attachments")
            .select("*")
            .eq("booking_id", booking_uuid)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as e:
        raise http_500(e)

    out: List[Dict[str, Any]] = []
    for att in res.data or []:
        bucket = _bucket_for(att.get("file_type") or "image")
        url = _signed_url(bucket, att.get("file_path") or "")
        out.append({**att, "url": url})
    return out


@router.post("/{booking_ref}/attachments")
async def upload_attachment(
    booking_ref: str,
    request: Request,
    file: UploadFile = File(...),
):
    """
    Staff (JWT): images + videos.
    Public (no JWT): images only, and only on recent Pending bookings.
    """
    _upload_limiter.check_or_raise(
        client_ip(request),
        detail="Too many uploads. Please wait a moment.",
    )

    staff_user = optional_owner(request)
    booking = _resolve_booking(booking_ref)
    booking_uuid = booking["id"]

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    is_image = content_type in IMAGE_MIMES
    is_video = content_type in VIDEO_MIMES

    if not is_image and not is_video:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    if is_video and not staff_user:
        raise HTTPException(status_code=403, detail="Video uploads require staff login")

    if not staff_user:
        # Public customer upload: only fresh Pending bookings
        if (booking.get("Status") or "") != "Pending":
            raise HTTPException(
                status_code=403,
                detail="Attachments can only be added while the booking is pending",
            )
        created = booking.get("created_at")
        if created:
            try:
                created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - created_dt > timedelta(hours=2):
                    raise HTTPException(
                        status_code=403,
                        detail="Upload window expired for this booking",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

    raw = await file.read()
    size = len(raw)
    if is_image and size > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 5 MB)")
    if is_video and size > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=400, detail="Video too large (max 100 MB)")
    if size < 32:
        raise HTTPException(status_code=400, detail="File empty or corrupt")

    file_type = "image" if is_image else "video"
    if file_type == "image" and _count_attachments(booking_uuid, "image") >= MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed")
    if file_type == "video" and _count_attachments(booking_uuid, "video") >= MAX_VIDEOS:
        raise HTTPException(status_code=400, detail="Only 1 video allowed per booking")

    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/quicktime": "mov",
    }
    ext = ext_map.get(content_type, "bin")
    path = f"{booking_uuid}/{uuid.uuid4().hex}.{ext}"
    bucket = _bucket_for(file_type)

    try:
        supabase.storage.from_(bucket).upload(
            path,
            raw,
            file_options={"content-type": content_type, "upsert": "false"},
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Storage upload failed: {e}") from e

    role = "owner" if staff_user else "customer"
    row = {
        "booking_id": booking_uuid,
        "uploaded_by_role": role,
        "file_path": path,
        "file_type": file_type,
        "mime_type": content_type,
        "size_bytes": size,
    }
    try:
        ins = supabase.table("booking_attachments").insert(row).execute()
        saved = ins.data[0] if ins.data else row
    except Exception as e:
        try:
            supabase.storage.from_(bucket).remove([path])
        except Exception:
            pass
        raise http_500(e)

    url = _signed_url(bucket, path)
    return {**saved, "url": url}
