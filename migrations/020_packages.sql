-- Slippy Goalz Arena — weekly packages / leagues
-- Run in Supabase → SQL Editor after deploying Phase 5 backend.

CREATE TABLE IF NOT EXISTS public.packages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id UUID REFERENCES public.customers(id),
  name TEXT,
  phone TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT 'Weekly package',
  start_date DATE NOT NULL,
  time TEXT NOT NULL,
  weeks INTEGER NOT NULL CHECK (weeks >= 2 AND weeks <= 12),
  amount_per_session NUMERIC,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_packages_customer_id ON public.packages (customer_id);
CREATE INDEX IF NOT EXISTS idx_packages_phone ON public.packages (phone);
CREATE INDEX IF NOT EXISTS idx_packages_created_at ON public.packages (created_at DESC);

ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS package_id UUID REFERENCES public.packages(id);

CREATE INDEX IF NOT EXISTS idx_bookings_package_id ON public.bookings (package_id);

-- RLS lockdown (service_role only; matches other arena tables)
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT policyname
    FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'packages'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.packages', r.policyname);
  END LOOP;

  ALTER TABLE public.packages ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.packages FORCE ROW LEVEL SECURITY;
  REVOKE ALL ON TABLE public.packages FROM PUBLIC;
  REVOKE ALL ON TABLE public.packages FROM anon;
  REVOKE ALL ON TABLE public.packages FROM authenticated;
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.packages TO service_role;
EXCEPTION
  WHEN undefined_table THEN NULL;
END $$;

NOTIFY pgrst, 'reload schema';
