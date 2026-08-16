from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator
from supabase import create_client

from app.auth import verify_token
from app.config import ALGORITHM, OWNER_USERNAME, SECRET_KEY, SUPABASE_URL, SUPABASE_KEY
from app.errors import http_500

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
_optional_bearer = HTTPBearer(auto_error=False)

_PUBLIC_SLOT_FIELDS = ("id", "Date", "Time", "Day", "Status")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _day_name(date_str: str) -> str:
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return WEEKDAYS[d.weekday()]
    except Exception:
        return ""


def _slot_id(row: dict):
    return row.get("id") or row.get("ID") or row.get("Slot ID")


class SlotCreate(BaseModel):
    date: str
    time: str
    status: Optional[str] = "Available"

    @field_validator("date", "time")
    @classmethod
    def non_empty(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("must not be empty")
        return str(v).strip()


class SlotUpdate(BaseModel):
    # Legacy fields (ignored if Status present)
    available: Optional[int] = None
    booked: Optional[int] = None
    Status: Optional[str] = None
    status: Optional[str] = None
    Date: Optional[str] = None
    Time: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None


class CopyDayBody(BaseModel):
    from_date: str
    to_date: str

    @field_validator("from_date", "to_date")
    @classmethod
    def non_empty(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("must not be empty")
        return str(v).strip()[:10]


class BulkCreateBody(BaseModel):
    date: str
    times: List[str]

    @field_validator("date")
    @classmethod
    def non_empty_date(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("must not be empty")
        return str(v).strip()[:10]


@router.get("/")
def get_slots(creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer)):
    """Public callers get Date/Time/Status only. Owner JWT gets full rows."""
    try:
        res = supabase.table("slots").select("*").order("Date").execute()
        rows = res.data or []
    except Exception as e:
        raise http_500(e)

    owner = False
    if creds and creds.credentials:
        try:
            payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
            owner = payload.get("sub") == OWNER_USERNAME
        except JWTError:
            owner = False

    if owner:
        return rows
    return [{k: row.get(k) for k in _PUBLIC_SLOT_FIELDS} for row in rows]


@router.post("/")
def create_slot(slot: SlotCreate, user=Depends(verify_token)):
    """Create an Available (or Blocked) slot for Date+Time."""
    date = slot.date[:10]
    time = slot.time.strip()
    status = (slot.status or "Available").strip()
    if status not in ("Available", "Blocked", "Booked"):
        status = "Available"

    existing = (
        supabase.table("slots")
        .select("*")
        .eq("Date", date)
        .eq("Time", time)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        # Re-open a blocked/empty slot if creating Available
        if status == "Available" and row.get("Status") in ("Blocked", "Booked") and (
            not row.get("Booked By") or row.get("Booked By") in ("EMPTY", "BLOCKED", "")
        ):
            upd = (
                supabase.table("slots")
                .update({
                    "Status": "Available",
                    "Booked By": "EMPTY",
                    "Phone": "EMPTY",
                    "Booking ID": "",
                })
                .eq("id", _slot_id(row))
                .execute()
            )
            return upd.data[0] if upd.data else row
        raise HTTPException(status_code=409, detail="A slot already exists for this date and time")

    payload = {
        "Date": date,
        "Time": time,
        "Day": _day_name(date),
        "Status": status,
        "Booked By": "BLOCKED" if status == "Blocked" else "EMPTY",
        "Phone": "EMPTY",
        "Booking ID": "",
    }
    try:
        res = supabase.table("slots").insert(payload).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to create slot")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.post("/bulk")
def create_slots_bulk(body: BulkCreateBody, user=Depends(verify_token)):
    """Create multiple Available slots for one date (skips existing times)."""
    date = body.date[:10]
    created = []
    skipped = []
    for raw in body.times:
        time = str(raw).strip()
        if not time:
            continue
        try:
            created.append(
                create_slot(SlotCreate(date=date, time=time, status="Available"), user)
            )
        except HTTPException as e:
            if e.status_code == 409:
                skipped.append(time)
            else:
                raise
    return {"created": created, "skipped": skipped, "date": date}


@router.post("/copy-day")
def copy_day(body: CopyDayBody, user=Depends(verify_token)):
    """Copy times from from_date onto to_date as Available (skip conflicts)."""
    src = (
        supabase.table("slots")
        .select("*")
        .eq("Date", body.from_date)
        .order("Time")
        .execute()
    )
    times = []
    for row in src.data or []:
        t = row.get("Time")
        if t and t not in times:
            times.append(t)
    if not times:
        raise HTTPException(status_code=404, detail="No slots found on the source date")
    return create_slots_bulk(BulkCreateBody(date=body.to_date, times=times), user)


@router.put("/{slot_id}")
def update_slot(slot_id: str, slot: SlotUpdate, user=Depends(verify_token)):
    try:
        data = {}
        status = slot.Status or slot.status
        if status is not None:
            status = str(status).strip()
            data["Status"] = status
            if status == "Available":
                data["Booked By"] = "EMPTY"
                data["Phone"] = "EMPTY"
                data["Booking ID"] = ""
            elif status == "Blocked":
                data["Booked By"] = "BLOCKED"
                data["Phone"] = "EMPTY"
                data["Booking ID"] = ""

        date = slot.Date or slot.date
        time = slot.Time or slot.time
        if date is not None:
            data["Date"] = str(date).strip()[:10]
            data["Day"] = _day_name(data["Date"])
        if time is not None:
            data["Time"] = str(time).strip()

        # Legacy no-op fields — ignore available/booked integers

        if not data:
            raise HTTPException(status_code=400, detail="No updates provided")

        res = supabase.table("slots").update(data).eq("id", slot_id).execute()
        if not res.data:
            # try string id variants
            res = supabase.table("slots").update(data).eq("ID", slot_id).execute()
        return res.data[0] if res.data else {}
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.delete("/{slot_id}")
def delete_slot(slot_id: str, user=Depends(verify_token)):
    try:
        res = supabase.table("slots").delete().eq("id", slot_id).execute()
        if not res.data:
            res = supabase.table("slots").delete().eq("ID", slot_id).execute()
        return {"ok": True, "deleted": res.data or []}
    except Exception as e:
        raise http_500(e)
