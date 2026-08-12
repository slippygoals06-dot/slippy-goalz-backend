-- CRITICAL: Enable RLS and deny all direct PostgREST access for anon/authenticated
-- Run in Supabase SQL Editor
--
-- Why this is safe for the app:
--   FastAPI uses SUPABASE_KEY (service_role) on Railway.
--   The service_role key bypasses RLS entirely — backend reads/writes keep working.
--
-- Frontend check (2026-07-19):
--   src/lib/supabase.js defines an anon client but is never imported by any UI code.
--   Public booking / chatbot go through FastAPI only — no anon policies needed.

-- ── 1. Drop any existing policies on these tables (deny-by-default after RLS) ──
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'bookings',
        'chat_sessions',
        'invoices',
        'slots',
        'cash_ledger',
        'audit_events'
      )
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON %I.%I',
      r.policyname,
      r.schemaname,
      r.tablename
    );
  END LOOP;
END $$;

-- ── 2. Enable + FORCE RLS; revoke table grants from API roles ─────────────────
-- FORCE applies RLS even to the table owner. service_role still bypasses RLS.
DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'bookings',
    'chat_sessions',
    'invoices',
    'slots',
    'cash_ledger',
    'audit_events'
  ]
  LOOP
    IF to_regclass(format('public.%I', t)) IS NULL THEN
      RAISE NOTICE 'Skipping missing table: %', t;
      CONTINUE;
    END IF;

    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);

    -- Defense in depth: even if RLS were disabled later, anon cannot touch rows
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', t);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated', t);

    RAISE NOTICE 'RLS enabled + grants revoked for: %', t;
  END LOOP;
END $$;

-- ── 3. Explicitly NO policies for anon/authenticated ──────────────────────────
-- With RLS on and zero policies, Postgres denies SELECT/INSERT/UPDATE/DELETE
-- for roles subject to RLS. Do not add USING (true) policies.

-- Verify after running (Supabase SQL Editor):
--   SELECT relname, relrowsecurity, relforcerowsecurity
--   FROM pg_class
--   WHERE relname IN (
--     'bookings','chat_sessions','invoices','slots','cash_ledger','audit_events'
--   );
--
-- Anon probe (must be empty or permission-denied — never customer rows):
--   curl "https://<PROJECT>.supabase.co/rest/v1/bookings?select=*&limit=1" \
--     -H "apikey: <ANON_KEY>" \
--     -H "Authorization: Bearer <ANON_KEY>"
