"""Weekly packages / leagues: one customer, many linked bookings."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from supabase import create_client

from app.auth import require_perm
from app.config import SUPABASE_KEY, SUPABASE_URL
from app.customers import find_or_create_customer
from app.errors import http_500
from app.phone import normalize_phone
from app.slot_claim import SLOT_UNAVAILABLE_MSG, claim_slot, link_slot_booking, release_slot

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

SERVICE_LABEL = "Pitch booking"


class PackageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    phone: str = Field(..., min_length=10, max_length=24)
    title: Optional[str] = Field("Weekly package", max_length=80)
    start_date: str = Field(..., min_length=8, max_length=16)
    time: str = Field(..., min_length=1, max_length=16)
    weeks: int = Field(..., ge=2, le=12)
    amount_per_session: Optional[float] = Field(None, ge=0, le=10_000_000)
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("start_date")
    @classmethod
    def valid_date(cls, v: str) -> str:
        s = str(v).strip()[:10]
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError("start_date must be YYYY-MM-DD") from e
        return s

    @field_validator("time")
    @classmethod
    def valid_time(cls, v: str) -> str:
        s = str(v).strip()
        # Accept HH:MM or already-normalized
        if len(s) >= 5 and s[2] == ":":
            return s[:5]
        raise ValueError("time must be HH:MM")


def _session_dates(start_date: str, weeks: int) -> List[str]:
    base = datetime.strptime(start_date, "%Y-%m-%d")
    return [(base + timedelta(days=7 * i)).strftime("%Y-%m-%d") for i in range(weeks)]


@router.get("/")
def list_packages(user=Depends(require_perm("bookings"))):
    try:
        res = (
            supabase.table("packages")
            .select("*")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return res.data or []
    except Exception as e:
        raise http_500(e)


@router.get("/{package_id}")
def get_package(package_id: str, user=Depends(require_perm("bookings"))):
    try:
        pkg = (
            supabase.table("packages")
            .select("*")
            .eq("id", package_id)
            .limit(1)
            .execute()
        )
        if not pkg.data:
            raise HTTPException(status_code=404, detail="Package not found")
        bookings = (
            supabase.table("bookings")
            .select("*")
            .eq("package_id", package_id)
            .order("Date", desc=False)
            .execute()
        )
        return {"package": pkg.data[0], "bookings": bookings.data or []}
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.post("/")
def create_weekly_package(body: PackageCreate, user=Depends(require_perm("bookings"))):
    """
    Create a weekly league/package: N bookings same weekday+time, linked by package_id.
    Claims slots first; rolls back all claims if any date is unavailable.
    """
    try:
        phone = normalize_phone(body.phone)
        if not phone:
            raise HTTPException(
                status_code=400,
                detail="Invalid phone number. Use format 03001234567 or +923001234567",
            )

        dates = _session_dates(body.start_date, body.weeks)
        booking_time = body.time[:5]

        # Pre-claim every slot (atomic) — all or nothing
        claims = []
        for d in dates:
            claimed = claim_slot(d, booking_time, phone)
            if not claimed:
                for c in claims:
                    if c.get("id") is not None:
                        release_slot(c["id"])
                raise HTTPException(
                    status_code=409,
                    detail=f"{SLOT_UNAVAILABLE_MSG} ({d} {booking_time})",
                )
            claims.append({"date": d, "slot": claimed})

        customer = None
        try:
            customer = find_or_create_customer(phone, name=body.name)
        except Exception as cust_err:
            print(f"find_or_create_customer failed: {cust_err}")

        title = (body.title or "Weekly package").strip() or "Weekly package"
        package_row = {
            "name": body.name.strip(),
            "phone": phone,
            "title": title,
            "start_date": body.start_date,
            "time": booking_time,
            "weeks": body.weeks,
            "status": "active",
        }
        if customer and customer.get("id"):
            package_row["customer_id"] = customer["id"]
        if body.amount_per_session is not None:
            package_row["amount_per_session"] = float(body.amount_per_session)

        try:
            pkg_res = supabase.table("packages").insert(package_row).execute()
            package = pkg_res.data[0] if pkg_res.data else None
            if not package:
                raise RuntimeError("Package insert returned empty")
        except Exception:
            for c in claims:
                if c["slot"].get("id") is not None:
                    release_slot(c["slot"]["id"])
            raise

        package_id = package["id"]
        created_bookings = []
        note_base = body.notes.strip() if body.notes else ""
        package_note = f"[Package] {title} ({body.weeks} weeks)"
        notes = f"{package_note}. {note_base}".strip() if note_base else package_note

        try:
            for c in claims:
                booking_id = f"CUST-{uuid.uuid4().hex[:8].upper()}"
                row = {
                    "Booking ID": booking_id,
                    "Name": body.name.strip(),
                    "Phone": phone,
                    "Service": SERVICE_LABEL,
                    "Issue": "Cash",
                    "Date": c["date"],
                    "Time": booking_time,
                    "Status": "Confirmed",
                    "Payment Status": "Unpaid",
                    "Notes": notes,
                    "package_id": package_id,
                    "Source": "Weekly package",
                }
                if customer and customer.get("id"):
                    row["customer_id"] = customer["id"]
                if body.amount_per_session is not None:
                    row["amount"] = float(body.amount_per_session)

                ins = supabase.table("bookings").insert(row).execute()
                booking = ins.data[0] if ins.data else row
                created_bookings.append(booking)
                slot_id = c["slot"].get("id")
                if slot_id is not None:
                    link_slot_booking(slot_id, booking_id)
        except Exception:
            for c in claims:
                if c["slot"].get("id") is not None:
                    release_slot(c["slot"]["id"])
            try:
                supabase.table("bookings").delete().eq("package_id", package_id).execute()
                supabase.table("packages").delete().eq("id", package_id).execute()
            except Exception:
                pass
            raise

        return {
            "package": package,
            "bookings": created_bookings,
            "weeks": body.weeks,
            "dates": dates,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)
