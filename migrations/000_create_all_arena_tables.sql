-- Slippy Goalz Arena — create ALL tables (empty database)
-- Paste into Supabase → SQL Editor → Run
--
-- Creates: bookings, slots, cash_ledger, chat_sessions, chats, integrations,
--          invoices, leads, parts_batches, services, waitlist, audit_events,
--          owner_settings
-- Does NOT create: part_events, parts_ledger, repair_ledger

-- ── bookings ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.bookings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "Booking ID" TEXT NOT NULL UNIQUE,
  "Name" TEXT NOT NULL,
  "Phone" TEXT NOT NULL,
  "Email" TEXT,
  "Device" TEXT,
  "Service" TEXT,
  "Issue" TEXT,
  "Date" TEXT NOT NULL,
  "Time" TEXT NOT NULL,
  "Status" TEXT NOT NULL DEFAULT 'Pending',
  "Payment Status" TEXT NOT NULL DEFAULT 'Unpaid',
  "Notes" TEXT,
  "Source" TEXT,
  amount NUMERIC,
  status_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE,
  reminder_urgent_sent BOOLEAN NOT NULL DEFAULT FALSE,
  reminder_sent BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bookings_date ON public.bookings ("Date" DESC);
CREATE INDEX IF NOT EXISTS idx_bookings_phone ON public.bookings ("Phone");
CREATE INDEX IF NOT EXISTS idx_bookings_status ON public.bookings ("Status");

CREATE OR REPLACE FUNCTION public.set_booking_status_changed_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW."Status" IS DISTINCT FROM OLD."Status" THEN
    NEW.status_changed_at := NOW();
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bookings_status_changed_at ON public.bookings;
CREATE TRIGGER trg_bookings_status_changed_at
  BEFORE UPDATE ON public.bookings
  FOR EACH ROW
  EXECUTE FUNCTION public.set_booking_status_changed_at();

-- ── slots ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.slots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "Date" TEXT NOT NULL,
  "Time" TEXT NOT NULL,
  "Day" TEXT,
  "Status" TEXT NOT NULL DEFAULT 'Available',
  "Booked By" TEXT DEFAULT 'EMPTY',
  "Phone" TEXT DEFAULT 'EMPTY',
  "Booking ID" TEXT DEFAULT '',
  UNIQUE ("Date", "Time")
);

CREATE INDEX IF NOT EXISTS idx_slots_status ON public.slots ("Status");

-- ── cash_ledger ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.cash_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  amount NUMERIC NOT NULL,
  entry_type TEXT NOT NULL
    CHECK (entry_type IN ('cash_drop', 'expense', 'payout')),
  reason TEXT NOT NULL,
  logged_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT cash_ledger_amount_sign CHECK (
    (entry_type = 'cash_drop' AND amount > 0)
    OR (entry_type IN ('expense', 'payout') AND amount < 0)
  )
);

CREATE INDEX IF NOT EXISTS idx_cash_ledger_created_at ON public.cash_ledger (created_at DESC);

-- ── chat_sessions ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.chat_sessions (
  session_id TEXT PRIMARY KEY,
  step TEXT,
  language TEXT,
  mode TEXT,
  collected JSONB NOT NULL DEFAULT '{}'::jsonb,
  history JSONB NOT NULL DEFAULT '[]'::jsonb,
  booking_id TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_booking_id ON public.chat_sessions (booking_id);

-- ── chats ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.chats (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT,
  phone TEXT,
  channel TEXT DEFAULT 'website',
  collected JSONB NOT NULL DEFAULT '{}'::jsonb,
  history JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chats_phone ON public.chats (phone);
CREATE INDEX IF NOT EXISTS idx_chats_created_at ON public.chats (created_at DESC);

-- ── integrations ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  channel TEXT NOT NULL UNIQUE,
  phone_number_id TEXT,
  waba_id TEXT,
  access_token TEXT,
  status TEXT NOT NULL DEFAULT 'not_connected',
  last_verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── invoices ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.invoices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id TEXT NOT NULL,
  amount NUMERIC NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'unpaid'
    CHECK (status IN ('paid', 'unpaid')),
  invoice_number TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_booking_id ON public.invoices (booking_id);
CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON public.invoices (created_at DESC);

-- ── leads ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.leads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "Name" TEXT,
  "Phone" TEXT,
  "Device" TEXT,
  "Issue" TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_created_at ON public.leads (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON public.leads ("Phone");

-- ── parts_batches (keep; no part_events / parts_ledger) ─────────────────────
CREATE TABLE IF NOT EXISTS public.parts_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_name TEXT NOT NULL,
  batch_number TEXT NOT NULL UNIQUE,
  part_type TEXT NOT NULL,
  received_date DATE NOT NULL DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── services ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  price NUMERIC,
  duration_minutes INTEGER,
  description TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── waitlist ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.waitlist (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "Name" TEXT,
  "Phone" TEXT,
  "Email" TEXT,
  "Service" TEXT,
  "Device" TEXT,
  "Issue" TEXT,
  "Preferred Day" TEXT,
  "Date Added" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_waitlist_phone ON public.waitlist ("Phone");
CREATE INDEX IF NOT EXISTS idx_waitlist_created_at ON public.waitlist (created_at DESC);

-- ── owner_settings (PIN) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.owner_settings (
  username TEXT PRIMARY KEY,
  pin_hash TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── audit_events (create + harden, append-only) ─────────────────────────────
CREATE TABLE IF NOT EXISTS public.audit_events (
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

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON public.audit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_booking_id ON public.audit_events (booking_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_action ON public.audit_events (action);

CREATE OR REPLACE FUNCTION public.prevent_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only and cannot be updated or deleted';
END;
$$;

DROP TRIGGER IF EXISTS audit_events_no_update ON public.audit_events;
DROP TRIGGER IF EXISTS audit_events_no_delete ON public.audit_events;

CREATE TRIGGER audit_events_no_update
  BEFORE UPDATE ON public.audit_events
  FOR EACH ROW
  EXECUTE FUNCTION public.prevent_audit_mutation();

CREATE TRIGGER audit_events_no_delete
  BEFORE DELETE ON public.audit_events
  FOR EACH ROW
  EXECUTE FUNCTION public.prevent_audit_mutation();

-- ── RLS lockdown: public/anon cannot read tables; app uses service_role ─────
DO $$
DECLARE
  t TEXT;
  r RECORD;
  keep TEXT[] := ARRAY[
    'bookings',
    'slots',
    'cash_ledger',
    'chat_sessions',
    'chats',
    'integrations',
    'invoices',
    'leads',
    'parts_batches',
    'services',
    'waitlist',
    'owner_settings',
    'audit_events'
  ];
BEGIN
  FOREACH t IN ARRAY keep
  LOOP
    FOR r IN
      SELECT policyname
      FROM pg_policies
      WHERE schemaname = 'public' AND tablename = t
    LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', r.policyname, t);
    END LOOP;

    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', t);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', t);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated', t);

    IF t = 'audit_events' THEN
      EXECUTE 'GRANT SELECT, INSERT ON TABLE public.audit_events TO service_role';
      EXECUTE 'REVOKE UPDATE, DELETE ON TABLE public.audit_events FROM service_role';
    ELSE
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO service_role', t);
    END IF;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
