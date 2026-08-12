from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Literal
from supabase import create_client
from app.config import SUPABASE_URL, SUPABASE_KEY
from app.auth import verify_token

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

EntryType = Literal["cash_drop", "expense", "payout"]


class CashLedgerCreate(BaseModel):
    """Client sends a positive amount; server applies sign from entry_type."""
    amount: float = Field(..., gt=0)
    entry_type: EntryType
    reason: str = Field(..., min_length=1)


def _signed_amount(entry_type: str, amount: float) -> float:
    abs_amount = abs(float(amount))
    if entry_type == "cash_drop":
        return abs_amount
    return -abs_amount


@router.get("/")
def list_cash_ledger(
    user=Depends(verify_token),
    limit: int = Query(200, ge=1, le=500),
):
    try:
        res = (
            supabase.table("cash_ledger")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_cash_ledger_entry(body: CashLedgerCreate, user=Depends(verify_token)):
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    try:
        row = {
            "amount": _signed_amount(body.entry_type, body.amount),
            "entry_type": body.entry_type,
            "reason": reason,
            "logged_by": user or "unknown",
        }
        res = supabase.table("cash_ledger").insert(row).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to create ledger entry")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
