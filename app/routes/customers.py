"""Authenticated customer profile + booking history."""
from fastapi import APIRouter, Depends, HTTPException

from supabase import create_client

from app.auth import verify_token
from app.config import SUPABASE_KEY, SUPABASE_URL
from app.errors import http_500

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


@router.get("/{customer_id}")
def get_customer(customer_id: str, user=Depends(verify_token)):
    try:
        res = (
            supabase.table("customers")
            .select("*")
            .eq("id", customer_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.get("/{customer_id}/bookings")
def get_customer_bookings(customer_id: str, user=Depends(verify_token)):
    """Bookings for this customer, newest session date/time first."""
    try:
        exists = (
            supabase.table("customers")
            .select("id")
            .eq("id", customer_id)
            .limit(1)
            .execute()
        )
        if not exists.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        res = (
            supabase.table("bookings")
            .select("*")
            .eq("customer_id", customer_id)
            .order("Date", desc=True)
            .execute()
        )
        rows = list(res.data or [])

        def sort_key(b):
            return (str(b.get("Date") or ""), str(b.get("Time") or ""))

        rows.sort(key=sort_key, reverse=True)
        return rows
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)
