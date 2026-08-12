"""Append-only audit event helper. Failures are logged but never raise."""
from typing import Any, Dict, Optional
from supabase import create_client
from app.config import SUPABASE_URL, SUPABASE_KEY

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def log_audit_event(
    *,
    actor: str,
    action: str,
    booking_id: Optional[str] = None,
    invoice_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert an audit row after a successful mutation. Never raises."""
    try:
        row = {
            "actor": actor or "unknown",
            "action": action,
            "details": details or {},
        }
        if booking_id is not None:
            row["booking_id"] = booking_id
        if invoice_id is not None:
            row["invoice_id"] = str(invoice_id)
        supabase.table("audit_events").insert(row).execute()
    except Exception as e:
        print(f"Audit log insert failed ({action}): {e}")
