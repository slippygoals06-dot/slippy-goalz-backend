-- Server-side audit trail for staff booking/invoice actions
-- Run once in Supabase SQL Editor, then click Retry on Audit Log

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

-- Keep locked down for anon (FastAPI service_role bypasses RLS)
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.audit_events FROM anon;
REVOKE ALL ON TABLE public.audit_events FROM authenticated;

-- Refresh PostgREST schema cache so /audit-events works immediately
NOTIFY pgrst, 'reload schema';
