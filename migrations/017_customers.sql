-- Slippy Goalz Arena — permanent customer identity
-- Run in Supabase → SQL Editor AFTER deploying backend that writes customer_id.
--
-- Creates:
--   public.customers (UUID id, UNIQUE normalized phone)
--   bookings.customer_id → customers.id
-- Safe backfill: one customer per normalized phone, relink bookings.
-- Does NOT delete booking history or rewrite Name/Phone/Email snapshots.

-- ── normalize helper (mirrors app/phone.py for PK mobiles) ───────────────────
CREATE OR REPLACE FUNCTION public.normalize_pk_phone(raw TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  digits TEXT;
BEGIN
  IF raw IS NULL OR btrim(raw) = '' THEN
    RETURN NULL;
  END IF;

  digits := regexp_replace(raw, '\D', '', 'g');

  IF length(digits) = 11 AND left(digits, 1) = '0' THEN
    RETURN '+92' || substr(digits, 2);
  END IF;
  IF length(digits) = 12 AND left(digits, 2) = '92' THEN
    RETURN '+' || digits;
  END IF;
  IF length(digits) = 10 THEN
    RETURN '+92' || digits;
  END IF;
  IF left(btrim(raw), 1) = '+' AND length(digits) >= 12 THEN
    RETURN '+' || digits;
  END IF;

  RETURN NULL;
END;
$$;

-- ── customers table ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.customers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  phone TEXT NOT NULL,
  name TEXT,
  email TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT customers_phone_unique UNIQUE (phone)
);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON public.customers (phone);
CREATE INDEX IF NOT EXISTS idx_customers_created_at ON public.customers (created_at DESC);

-- ── bookings.customer_id ──────────────────────────────────────────────────────
ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS customer_id UUID REFERENCES public.customers(id);

CREATE INDEX IF NOT EXISTS idx_bookings_customer_id ON public.bookings (customer_id);

-- ── Backfill customers from distinct normalized booking phones ────────────────
-- Use aggregates (not correlated subqueries) so GROUP BY is valid in Postgres.
INSERT INTO public.customers (phone, name, email, created_at, updated_at)
SELECT
  n.phone,
  (array_agg(n.name ORDER BY n.created_at DESC NULLS LAST))[1] AS name,
  (
    array_agg(n.email ORDER BY n.created_at DESC NULLS LAST)
      FILTER (WHERE n.email IS NOT NULL AND btrim(n.email) <> '')
  )[1] AS email,
  MIN(n.created_at) AS created_at,
  NOW() AS updated_at
FROM (
  SELECT
    public.normalize_pk_phone(b."Phone") AS phone,
    b."Name" AS name,
    b."Email" AS email,
    b.created_at
  FROM public.bookings b
) n
WHERE n.phone IS NOT NULL
GROUP BY n.phone
ON CONFLICT (phone) DO NOTHING;

-- Relink bookings to canonical customer (does not change Name/Phone/Email)
UPDATE public.bookings b
SET customer_id = c.id
FROM public.customers c
WHERE b.customer_id IS NULL
  AND public.normalize_pk_phone(b."Phone") = c.phone;

-- ── RLS lockdown (service_role only; matches other arena tables) ──────────────
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT policyname
    FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'customers'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.customers', r.policyname);
  END LOOP;

  ALTER TABLE public.customers ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.customers FORCE ROW LEVEL SECURITY;
  REVOKE ALL ON TABLE public.customers FROM PUBLIC;
  REVOKE ALL ON TABLE public.customers FROM anon;
  REVOKE ALL ON TABLE public.customers FROM authenticated;
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.customers TO service_role;
END $$;

NOTIFY pgrst, 'reload schema';
