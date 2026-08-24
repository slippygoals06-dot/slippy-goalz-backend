-- Slippy Goalz Arena — security lockdown (uploads, rate limits, RPC grants)
-- Run in Supabase → SQL Editor after deploy.

-- ── Shared rate-limit (multi-instance Railway safe) ───────────────────────────
CREATE TABLE IF NOT EXISTS public.rate_limit_buckets (
  bucket_key TEXT PRIMARY KEY,
  window_start TIMESTAMPTZ NOT NULL,
  hit_count INT NOT NULL DEFAULT 0
);

ALTER TABLE public.rate_limit_buckets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_limit_buckets FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.rate_limit_buckets FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.rate_limit_hit(
  p_key text,
  p_window_seconds int,
  p_max int
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_start timestamptz;
  v_count int;
  v_now timestamptz := now();
BEGIN
  IF p_key IS NULL OR btrim(p_key) = '' OR p_window_seconds < 1 OR p_max < 1 THEN
    RETURN TRUE;
  END IF;

  INSERT INTO public.rate_limit_buckets(bucket_key, window_start, hit_count)
  VALUES (btrim(p_key), v_now, 1)
  ON CONFLICT (bucket_key) DO UPDATE
  SET
    window_start = CASE
      WHEN public.rate_limit_buckets.window_start
           + make_interval(secs => p_window_seconds) <= v_now
      THEN v_now
      ELSE public.rate_limit_buckets.window_start
    END,
    hit_count = CASE
      WHEN public.rate_limit_buckets.window_start
           + make_interval(secs => p_window_seconds) <= v_now
      THEN 1
      ELSE public.rate_limit_buckets.hit_count + 1
    END
  RETURNING window_start, hit_count INTO v_start, v_count;

  RETURN v_count > p_max;
END;
$$;

REVOKE ALL ON FUNCTION public.rate_limit_hit(text, int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.rate_limit_hit(text, int, int) TO service_role;

-- ── Slot claim RPCs: service_role only (no Supabase Auth callers) ────────────
REVOKE ALL ON FUNCTION public.claim_slot_atomic(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_slot_atomic(text, text, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_slot_atomic(text, text, text) TO service_role;

REVOKE ALL ON FUNCTION public.release_slot_atomic(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_slot_atomic(uuid) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_slot_atomic(uuid) TO service_role;

REVOKE ALL ON FUNCTION public.release_slot_by_datetime(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_slot_by_datetime(text, text, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_slot_by_datetime(text, text, text) TO service_role;

REVOKE ALL ON FUNCTION public.link_slot_booking_atomic(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.link_slot_booking_atomic(uuid, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.link_slot_booking_atomic(uuid, text) TO service_role;

-- ── booking_attachments: deny browser/anon; FastAPI service_role only ────────
DO $$
DECLARE
  r RECORD;
BEGIN
  IF to_regclass('public.booking_attachments') IS NULL THEN
    CREATE TABLE public.booking_attachments (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      booking_id UUID NOT NULL REFERENCES public.bookings(id) ON DELETE CASCADE,
      uploaded_by_role TEXT NOT NULL
        CHECK (uploaded_by_role IN ('customer', 'staff', 'manager', 'owner')),
      uploaded_by_user UUID,
      file_path TEXT NOT NULL,
      file_type TEXT NOT NULL CHECK (file_type IN ('image', 'video')),
      mime_type TEXT,
      size_bytes INT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_booking_attachments_booking
      ON public.booking_attachments (booking_id);
  END IF;

  FOR r IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'booking_attachments'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.booking_attachments', r.policyname);
  END LOOP;

  ALTER TABLE public.booking_attachments ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.booking_attachments FORCE ROW LEVEL SECURITY;
  REVOKE ALL ON TABLE public.booking_attachments FROM anon, authenticated;
END $$;

-- Lock storage buckets to private (service_role uploads via API)
UPDATE storage.buckets
SET public = false
WHERE id IN ('booking-images', 'booking-videos');

-- Drop permissive storage policies if any (service_role bypasses)
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'storage' AND tablename = 'objects'
      AND (
        policyname ILIKE '%booking%image%'
        OR policyname ILIKE '%booking%video%'
        OR policyname ILIKE '%Auth users upload%'
        OR policyname ILIKE '%Auth users read%'
        OR policyname ILIKE '%Staff upload%'
        OR policyname ILIKE '%Staff read%'
      )
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON storage.objects', r.policyname);
  END LOOP;
EXCEPTION WHEN undefined_table THEN
  NULL;
END $$;

NOTIFY pgrst, 'reload schema';
