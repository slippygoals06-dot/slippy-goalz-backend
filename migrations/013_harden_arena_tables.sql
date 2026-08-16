-- Slippy Goalz Arena: keep booking tables, drop unused repair ledgers, harden audit logs.
-- Run in Supabase → SQL Editor (project rpigjnkbkhpqvxgoljoj).

-- ── 1. Remove unused repair/parts ledger tables ──────────────────────────────
DROP TABLE IF EXISTS public.part_events CASCADE;
DROP TABLE IF EXISTS public.parts_ledger CASCADE;
DROP TABLE IF EXISTS public.repair_ledger CASCADE;

-- ── 2. Harden audit_events: RLS + no public access + append-only ─────────────
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all" ON public.audit_events;
DROP POLICY IF EXISTS "Enable read access for all users" ON public.audit_events;
DROP POLICY IF EXISTS "Enable insert for authenticated users only" ON public.audit_events;

REVOKE ALL ON TABLE public.audit_events FROM PUBLIC;
REVOKE ALL ON TABLE public.audit_events FROM anon;
REVOKE ALL ON TABLE public.audit_events FROM authenticated;

-- Backend (service_role) can insert + read. Nobody can update/delete via API.
GRANT SELECT, INSERT ON TABLE public.audit_events TO service_role;
REVOKE UPDATE, DELETE ON TABLE public.audit_events FROM service_role;

CREATE OR REPLACE FUNCTION public.prevent_audit_mutation()
RETURNS trigger
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

-- ── 3. Lock remaining app tables (fixes UNRESTRICTED chats / waitlist) ───────
DO $$
DECLARE
  t TEXT;
  r RECORD;
  keep TEXT[] := ARRAY[
    'bookings',
    'cash_ledger',
    'chat_sessions',
    'chats',
    'integrations',
    'invoices',
    'leads',
    'parts_batches',
    'services',
    'slots',
    'waitlist',
    'audit_events',
    'owner_settings'
  ];
BEGIN
  FOREACH t IN ARRAY keep
  LOOP
    IF to_regclass(format('public.%I', t)) IS NULL THEN
      RAISE NOTICE 'Skipping missing table: %', t;
      CONTINUE;
    END IF;

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

    RAISE NOTICE 'RLS locked: %', t;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
