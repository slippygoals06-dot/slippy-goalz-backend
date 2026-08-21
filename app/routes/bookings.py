import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from supabase import create_client

from app.appointment_time import parse_appointment_datetime
from app.audit import log_audit_event
from app.auth import require_perm, optional_owner
from app.config import SUPABASE_URL, SUPABASE_KEY, WHATSAPP_CONFIRM_TEMPLATE
from app.errors import http_500
from app.customers import find_or_create_customer
from app.phone import normalize_phone
from app.rate_limit import SlidingWindowRateLimiter, client_ip
from app.routes.reminders import send_booking_confirmation, schedule_reminder
from app.slot_claim import (
    SLOT_UNAVAILABLE_MSG,
    ACTIVE_BOOKING_STATUSES,
    RELEASE_ON_STATUSES,
    claim_slot,
    link_slot_booking,
    release_slot,
    release_slot_by_datetime,
    is_unique_violation,
)
from app.whatsapp import send_whatsapp_message
from app.wa_copy import confirm_body_params


router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# your business name - hardcode for now, or pull from a settings table later
BUSINESS_NAME = "Slippy Goalz Arena"

# 5 booking creates per IP per 60s (spam protection; one real booking is fine)
_booking_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
SERVICE_LABEL = "Pitch booking"


def normalize_payment_mode(value: Optional[str]) -> str:
    raw = str(value or "").strip().lower()
    if raw == "online":
        return "Online"
    return "Cash"


class Booking(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    phone: str = Field(..., min_length=10, max_length=24)
    email: Optional[str] = Field(None, max_length=120)
    device: Optional[str] = Field(None, max_length=40)
    service: Optional[str] = Field(None, max_length=80)
    issue: Optional[str] = Field(None, max_length=80)
    payment_mode: Optional[str] = Field(None, max_length=16)
    date: str = Field(..., min_length=1, max_length=32)
    time: str = Field(..., min_length=1, max_length=32)
    status: Optional[str] = None
    payment_status: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=500)
    amount: Optional[float] = Field(None, ge=0, le=10_000_000)
    source: Optional[str] = Field(None, max_length=40)

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
    amount: Optional[float] = Field(None, ge=0, le=10_000_000)
    deposit_amount: Optional[float] = Field(None, ge=0, le=10_000_000)
    deposit_paid: Optional[bool] = None
    source: Optional[str] = None

class StatusUpdate(BaseModel):
    Status: str

class PaymentUpdate(BaseModel):
    payment_status: str


def _normalize_idempotency_key(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    key = str(raw).strip()
    if not key:
        return None
    if len(key) > 64:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be at most 64 characters")
    return key


def _booking_by_idempotency_key(key: str) -> Optional[dict]:
    res = (
        supabase.table("bookings")
        .select("*")
        .eq("idempotency_key", key)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _send_confirm_whatsapp(booking: dict) -> None:
    """Best-effort WhatsApp confirmation; never raises into the request."""
    if booking.get("confirmation_wa_sent"):
        return
    phone = booking.get("Phone") or ""
    if not phone:
        return
    try:
        result = send_whatsapp_message(
            phone,
            WHATSAPP_CONFIRM_TEMPLATE,
            body_params=confirm_body_params(booking),
        )
        if result.get("ok"):
            supabase.table("bookings").update({"confirmation_wa_sent": True}).eq(
                "Booking ID", booking.get("Booking ID")
            ).execute()
        else:
            print(f"WhatsApp confirm failed: {result.get('error')}")
    except Exception as wa_err:
        print(f"WhatsApp confirm error: {wa_err}")


@router.get("/")
def get_bookings(user=Depends(require_perm("bookings"))):
    try:
        res = supabase.table("bookings").select("*").order("Date", desc=True).execute()
        return res.data  # plain array
    except Exception as e:
        raise http_500(e)

@router.get("/{booking_id}/history")
def get_booking_history(booking_id: str, user=Depends(require_perm("bookings"))):
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
        raise http_500(e)

@router.get("/{booking_id}")
def get_booking(booking_id: str, user=Depends(require_perm("bookings"))):
    try:
        res = supabase.table("bookings").select("*").eq("Booking ID", booking_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Booking not found")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)

@router.post("/")
def create_booking(booking: Booking, request: Request):
    _booking_limiter.check_or_raise(
        client_ip(request),
        detail="Too many booking requests. Please wait a moment and try again.",
    )
    try:
        idem_key = _normalize_idempotency_key(request.headers.get("Idempotency-Key"))
        if idem_key:
            existing = _booking_by_idempotency_key(idem_key)
            if existing:
                return existing

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

        # Soft duplicate guard — DB unique index is the hard race net
        try:
            dup = (
                supabase.table("bookings")
                .select("Booking ID, Status")
                .eq("Phone", phone)
                .eq("Date", booking_date)
                .in_("Status", list(ACTIVE_BOOKING_STATUSES))
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

        # Claim slot FIRST (atomic RPC / Available→Booked). Reject if 0 rows.
        claimed = claim_slot(booking_date, booking_time, phone)
        if not claimed:
            raise HTTPException(status_code=409, detail=SLOT_UNAVAILABLE_MSG)

        payment_mode = normalize_payment_mode(booking.payment_mode or booking.issue)

        customer = None
        try:
            customer = find_or_create_customer(
                phone, name=booking.name, email=booking.email
            )
        except Exception as cust_err:
            print(f"find_or_create_customer failed (continuing): {cust_err}")

        slot_id = claimed.get("id")
        booking_id = f"CUST-{uuid.uuid4().hex[:8].upper()}"
        data = {
            "Booking ID": booking_id,
            "Name": booking.name,
            "Phone": phone,
            "Email": booking.email,
            "Device": booking.device,
            "Service": booking.service or SERVICE_LABEL,
            "Issue": payment_mode,
            "Date": booking_date,
            "Time": booking_time,
            "Status": "Pending",
            "Payment Status": "Unpaid",
            "Notes": booking.notes,
        }
        if customer and customer.get("id"):
            data["customer_id"] = customer["id"]
        if idem_key:
            data["idempotency_key"] = idem_key
        if optional_owner(request) and booking.amount is not None:
            data["amount"] = float(booking.amount)
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
        except Exception as insert_err:
            if idem_key:
                raced = _booking_by_idempotency_key(idem_key)
                if raced:
                    if slot_id is not None:
                        release_slot(slot_id)
                    return raced
            if slot_id is not None:
                release_slot(slot_id)
            if is_unique_violation(insert_err):
                raise HTTPException(status_code=409, detail=SLOT_UNAVAILABLE_MSG) from insert_err
            raise insert_err

        # --- NEW: send confirmation + schedule reminder ---
        if booking.email:
            try:
                send_booking_confirmation(
                    customer_email=booking.email,
                    business_name=BUSINESS_NAME,
                    service=booking.service or "Pitch booking",
                    appointment_time=appointment_dt,
                )
                schedule_reminder(
                    customer_email=booking.email,
                    business_name=BUSINESS_NAME,
                    service=booking.service or "Pitch booking",
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
        raise http_500(e)

@router.put("/{booking_id}")
def update_booking(booking_id: str, booking: BookingUpdate, user=Depends(require_perm("bookings"))):
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

        data = {}
        # Customer identity fields must be immutable to keep customer history accurate.
        if booking.service is not None:
            data["Service"] = booking.service
        if booking.issue is not None:
            data["Issue"] = booking.issue
        if booking.status is not None:
            data["Status"] = booking.status
        if booking.payment_status is not None:
            data["Payment Status"] = booking.payment_status
        if booking.notes is not None:
            data["Notes"] = booking.notes
        if booking.amount is not None:
            data["amount"] = booking.amount
        if booking.deposit_amount is not None:
            data["deposit_amount"] = booking.deposit_amount
        if booking.deposit_paid is not None:
            data["deposit_paid"] = booking.deposit_paid
        if booking.source is not None:
            data["Source"] = booking.source

        old_date = before.get("Date")
        old_time = before.get("Time")
        new_date = booking.date if booking.date is not None else old_date
        new_time = booking.time if booking.time is not None else old_time

        # Canonicalize if date/time provided
        moved = False
        claimed_new = None
        if booking.date is not None or booking.time is not None:
            appointment_dt = parse_appointment_datetime(new_date, new_time)
            if not appointment_dt:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid date/time format. Use date YYYY-MM-DD and time HH:MM.",
                )
            new_date = appointment_dt.strftime("%Y-%m-%d")
            new_time = appointment_dt.strftime("%H:%M")
            data["Date"] = new_date
            data["Time"] = new_time
            moved = (new_date != old_date) or (new_time != old_time)

        new_status = data.get("Status", before.get("Status"))
        will_be_active = new_status in ACTIVE_BOOKING_STATUSES
        was_active = before.get("Status") in ACTIVE_BOOKING_STATUSES

        # Moving an active booking → claim new hour first, then free old
        if moved and will_be_active:
            phone = before.get("Phone") or ""
            claimed_new = claim_slot(new_date, new_time, phone)
            if not claimed_new:
                raise HTTPException(status_code=409, detail=SLOT_UNAVAILABLE_MSG)

        try:
            res = supabase.table("bookings").update(data).eq("Booking ID", booking_id).execute()
        except Exception as upd_err:
            if claimed_new and claimed_new.get("id") is not None:
                release_slot(claimed_new["id"])
            if is_unique_violation(upd_err):
                raise HTTPException(status_code=409, detail=SLOT_UNAVAILABLE_MSG) from upd_err
            raise

        if not res.data:
            if claimed_new and claimed_new.get("id") is not None:
                release_slot(claimed_new["id"])
            raise HTTPException(status_code=404, detail="Booking not found")

        updated = res.data[0]

        if moved and will_be_active:
            if claimed_new and claimed_new.get("id") is not None:
                link_slot_booking(claimed_new["id"], booking_id)
            release_slot_by_datetime(old_date, old_time, booking_id)

        # Status moved to free-slot → release hour
        if new_status in RELEASE_ON_STATUSES and was_active:
            release_slot_by_datetime(
                updated.get("Date") or old_date,
                updated.get("Time") or old_time,
                booking_id,
            )

        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)

@router.put("/{booking_id}/status")
def update_booking_status(booking_id: str, body: StatusUpdate, user=Depends(require_perm("bookings"))):
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

        updated = res.data[0]
        action = None
        if body.Status == "Confirmed":
            action = "confirmed"
            _send_confirm_whatsapp(updated)
        elif body.Status == "Rejected":
            action = "rejected"
        elif body.Status in ("No-show", "No Show", "Noshow"):
            action = "no_show"
        if action:
            log_audit_event(
                actor=user,
                action=action,
                booking_id=booking_id,
                details={
                    "name": before.get("Name"),
                    "from": before.get("Status"),
                    "to": body.Status,
                    "deposit_paid": before.get("deposit_paid"),
                    "deposit_amount": before.get("deposit_amount"),
                },
            )

        # Free pitch hour when booking no longer holds the slot
        if body.Status in RELEASE_ON_STATUSES:
            release_slot_by_datetime(before.get("Date"), before.get("Time"), booking_id)

        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)

@router.put("/{booking_id}/payment")
def update_booking_payment(booking_id: str, body: PaymentUpdate, user=Depends(require_perm("bookings"))):
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
        raise http_500(e)

@router.delete("/{booking_id}")
def delete_booking(booking_id: str, user=Depends(require_perm("bookings"))):
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

        if before:
            release_slot_by_datetime(before.get("Date"), before.get("Time"), booking_id)

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
        raise http_500(e)