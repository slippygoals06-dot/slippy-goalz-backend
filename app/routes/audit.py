from fastapi import APIRouter, HTTPException, Depends, Query
from supabase import create_client
from app.config import SUPABASE_URL, SUPABASE_KEY
from app.auth import verify_token
from app.errors import http_500

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

MISSING_TABLE_HINTS = (
    "PGRST205",
    "does not exist",
    "Could not find the table",
    'relation "audit_events" does not exist',
    "relation 'audit_events' does not exist",
)

SETUP_SQL = """-- Server-side audit trail for staff booking/invoice actions
-- Run once in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS audit_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor TEXT NOT NULL,
  action TEXT NOT NULL
    CHECK (action IN (
      'confirmed',
      'rejected',
      'deleted',
      'payment_changed',
      'completed_invoiced',
      'invoice_status_changed'
    )),
  booking_id TEXT,
  invoice_id TEXT,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_booking_id ON audit_events(booking_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events(action);

ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.audit_events FROM anon;
REVOKE ALL ON TABLE public.audit_events FROM authenticated;

NOTIFY pgrst, 'reload schema';
"""


def _is_missing_table(err: Exception) -> bool:
    msg = str(err)
    return any(h.lower() in msg.lower() for h in MISSING_TABLE_HINTS)


@router.get("/")
def list_audit_events(
    user=Depends(verify_token),
    limit: int = Query(200, ge=1, le=500),
):
    """Return recent audit events, newest first."""
    try:
        res = (
            supabase.table("audit_events")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        if _is_missing_table(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    "PGRST205: Could not find the table 'public.audit_events' in the schema cache. "
                    "Run migrations/006_audit_events.sql in the Supabase SQL Editor, then Retry."
                ),
            )
        raise http_500(e)


@router.get("/setup-sql")
def audit_setup_sql(user=Depends(verify_token)):
    """Return the SQL required to create the audit_events table."""
    return {
        "filename": "006_audit_events.sql",
        "sql": SETUP_SQL,
        "instructions": [
            "Open Supabase → SQL Editor",
            "Paste and run the SQL",
            "Return here and click Retry Connection",
        ],
    }
