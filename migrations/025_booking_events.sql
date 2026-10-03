-- The Manager booking funnel events.
-- Run in Supabase SQL Editor before deploying the event-writing backend.

CREATE TABLE IF NOT EXISTS public.booking_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_ref TEXT,
  customer_ref TEXT NOT NULL CHECK (btrim(customer_ref) <> ''),
  event_type TEXT NOT NULL
    CHECK (event_type IN ('booking_started', 'booking_confirmed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_booking_events_created_at
  ON public.booking_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_booking_events_customer_type_created
  ON public.booking_events (customer_ref, event_type, created_at);

CREATE INDEX IF NOT EXISTS idx_booking_events_booking_type
  ON public.booking_events (booking_ref, event_type)
  WHERE booking_ref IS NOT NULL;

ALTER TABLE public.booking_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.booking_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.booking_events FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.booking_events TO service_role;

COMMENT ON TABLE public.booking_events IS
  'Deterministic customer booking-start and successful-submission events.';

NOTIFY pgrst, 'reload schema';
