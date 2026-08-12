import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from supabase import create_client

from app.appointment_time import parse_appointment_datetime
from app.audit import log_audit_event
from app.auth import verify_token
from app.config import SUPABASE_URL, SUPABASE_KEY
from app.phone import normalize_phone
from app.pricing import calculate_booking_amount
from app.rate_limit import SlidingWindowRateLimiter, client_ip
from app.routes.reminders import send_booking_confirmation, schedule_reminder
from app.slot_claim import SLOT_UNAVAILABLE_MSG, claim_slot, link_slot_booking, release_slot

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# your business name - hardcode for now, or pull from a settings table later
BUSINESS_NAME = "Slippy Goalz"

# 5 booking creates per IP per 60s (spam protection; one real booking is fine)
_booking_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


class Booking(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    device: Optional[str] = None
    service: Optional[str] = None
    issue: Optional[str] = None
    date: str
    time: str
    status: Optional[str] = "Pending"
    payment_status: Optional[str] = "Unpaid"
    notes: Optional[str] = None
    amount: Optional[float] = None  # ignored on create — server recalculates
    source: Optional[str] = None

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if not _EMAIL_RE.match(s):
            raise ValueError("Invalid email format")
        return s

    @field_validator("date", "time")
    @classmethod
    def non_empty_str(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("must not be empty")
        return str(v).strip()

class BookingUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    device: Optional[str] = None
    service: Optional[str] = None
    issue: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    status: Optional[str] = None
    payment_status: Optional[str] = None
    notes: Optional[str] = None
    amount: Optional[float] = None
    source: Optional[str] = None

class StatusUpdate(BaseModel):
    Status: str

class PaymentUpdate(BaseModel):
    payment_status: str


@router.get("/")
def get_bookings(user=Depends(verify_token)):
    try:
        res = supabase.table("bookings").select("*").order("Date", desc=True).execute()
        return res.data  # plain array
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{booking_id}/history")
def get_booking_history(booking_id: str, user=Depends(verify_token)):
    """Return the chatbot conversation history linked to this booking (read-only)."""
    try:
        res = (
            supabase.table("chat_sessions")
            .select("history, updated_at, session_id")
            .eq("booking_id", booking_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return []
        history = res.data[0].get("history") or []
        # history is already chronological; no per-message timestamps stored
        return history
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{booking_id}")
def get_booking(booking_id: str, user=Depends(verify_token)):
    try:
        res = supabase.table("bookings").select("*").eq("Booking ID", booking_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Booking not found")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
def create_booking(booking: Booking, request: Request):
    _booking_limiter.check_or_raise(
        client_ip(request),
        detail="Too many booking requests. Please wait a moment and try again.",
    )
    try:
        phone = normalize_phone(booking.phone)
        if not phone:
            raise HTTPException(
                status_code=400,
                detail="Invalid phone number. Use format 03001234567 or +923001234567",
            )

        appointment_dt = parse_appointment_datetime(booking.date, booking.time)
        if not appointment_dt:
            raise HTTPException(
                status_code=400,
                detail="Invalid date/time format. Use date YYYY-MM-DD and time HH:MM (or 12h e.g. 2:00 PM).",
            )
        # Canonical forms for slot matching + storage
        booking_date = appointment_dt.strftime("%Y-%m-%d")
        booking_time = appointment_dt.strftime("%H:%M")

        # Same duplicate guard as chatbot: one booking per phone+date
        try:
            dup = (
                supabase.table("bookings")
                .select("Booking ID")
                .eq("Phone", phone)
                .eq("Date", booking_date)
                .execute()
            )
            if dup.data:
                raise HTTPException(
                    status_code=409,
                    detail=f"A booking already exists for this phone on {booking_date}",
                )
        except HTTPException:
            raise
        except Exception as dup_err:
            print(f"Duplicate check failed (continuing): {dup_err}")

        # Claim slot FIRST (atomic WHERE Status=Available). Reject if 0 rows.
        claimed = claim_slot(booking_date, booking_time, phone)
        if not claimed:
            raise HTTPException(status_code=409, detail=SLOT_UNAVAILABLE_MSG)

        # Never trust client amount — recalculate from tier pricing when possible
        server_amount = calculate_booking_amount(booking.device, booking.service)

        slot_id = claimed.get("id")
        booking_id = f"CUST-{uuid.uuid4().hex[:8].upper()}"
        data = {
            "Booking ID": booking_id,
            "Name": booking.name,
            "Phone": phone,
            "Email": booking.email,
            "Device": booking.device,
            "Service": booking.service,
            "Issue": booking.issue,
            "Date": booking_date,
            "Time": booking_time,
            "Status": booking.status,
            "Payment Status": booking.payment_status,
            "Notes": booking.notes,
        }
        if server_amount is not None:
            data["amount"] = server_amount
        if booking.source:
            data["Source"] = booking.source

        try:
            res = supabase.table("bookings").insert(data).execute()
            result = res.data[0] if res.data else {}
            if slot_id is not None:
                try:
                    link_slot_booking(slot_id, booking_id)
                except Exception as link_err:
                    print(f"Slot Booking ID link failed (booking still saved): {link_err}")
        except Exception:
            if slot_id is not None:
                release_slot(slot_id)
            raise

        # --- NEW: send confirmation + schedule reminder ---
        if booking.email:
            try:
                send_booking_confirmation(
                    customer_email=booking.email,
                    business_name=BUSINESS_NAME,
                    service=booking.service or "repair",
                    appointment_time=appointment_dt,
                )
                schedule_reminder(
                    customer_email=booking.email,
                    business_name=BUSINESS_NAME,
                    service=booking.service or "repair",
                    appointment_time=appointment_dt,
                )
            except Exception as email_err:
                # don't let email failures break the booking itself
                print(f"Confirmation/reminder email failed: {email_err}")
        # --- END NEW ---

        return result
    except HTTPException:
        raise
    except Exception as e:
        # Surface Pydantic-style validation messages cleanly if raised as ValueError
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{booking_id}")
def update_booking(booking_id: str, booking: BookingUpdate, user=Depends(verify_token)):
    try:
        data = {}
        if booking.name is not None: data["Name"] = booking.name
        if booking.phone is not None:
            phone = normalize_phone(booking.phone)
            if not phone:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid phone number. Use format 03001234567 or +923001234567",
                )
            data["Phone"] = phone
        if booking.email is not None: data["Email"] = booking.email
        if booking.device is not None: data["Device"] = booking.device
        if booking.service is not None: data["Service"] = booking.service
        if booking.issue is not None: data["Issue"] = booking.issue
        if booking.date is not None: data["Date"] = booking.date
        if booking.time is not None: data["Time"] = booking.time
        if booking.status is not None: data["Status"] = booking.status
        if booking.payment_status is not None: data["Payment Status"] = booking.payment_status
        if booking.notes is not None: data["Notes"] = booking.notes
        if booking.amount is not None: data["amount"] = booking.amount
        if booking.source is not None: data["Source"] = booking.source
        res = supabase.table("bookings").update(data).eq("Booking ID", booking_id).execute()
        return res.data[0] if res.data else {}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{booking_id}/status")
def update_booking_status(booking_id: str, body: StatusUpdate, user=Depends(verify_token)):
    try:
        existing = (
            supabase.table("bookings")
            .select("*")
            .eq("Booking ID", booking_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Booking not found")
        before = existing.data[0]

        res = (
            supabase.table("bookings")
            .update({"Status": body.Status})
            .eq("Booking ID", booking_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Booking not found")

        action = None
        if body.Status == "Confirmed":
            action = "confirmed"
        elif body.Status == "Rejected":
            action = "rejected"
        if action:
            log_audit_event(
                actor=user,
                action=action,
                booking_id=booking_id,
                details={
                    "name": before.get("Name"),
                    "from": before.get("Status"),
                    "to": body.Status,
                },
            )
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{booking_id}/payment")
def update_booking_payment(booking_id: str, body: PaymentUpdate, user=Depends(verify_token)):
    try:
        existing = (
            supabase.table("bookings")
            .select("*")
            .eq("Booking ID", booking_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Booking not found")
        before = existing.data[0]

        res = (
            supabase.table("bookings")
            .update({"Payment Status": body.payment_status})
            .eq("Booking ID", booking_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Booking not found")

        log_audit_event(
            actor=user,
            action="payment_changed",
            booking_id=booking_id,
            details={
                "name": before.get("Name"),
                "from": before.get("Payment Status"),
                "to": body.payment_status,
            },
        )
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{booking_id}")
def delete_booking(booking_id: str, user=Depends(verify_token)):
    try:
        existing = (
            supabase.table("bookings")
            .select("*")
            .eq("Booking ID", booking_id)
            .limit(1)
            .execute()
        )
        before = existing.data[0] if existing.data else None

        supabase.table("bookings").delete().eq("Booking ID", booking_id).execute()

        log_audit_event(
            actor=user,
            action="deleted",
            booking_id=booking_id,
            details={
                "name": (before or {}).get("Name"),
                "status": (before or {}).get("Status"),
                "date": (before or {}).get("Date"),
                "time": (before or {}).get("Time"),
            },
        )
        return {"message": "Booking deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))