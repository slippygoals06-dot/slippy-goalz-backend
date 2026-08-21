-- Slippy Goalz Arena — atomic slot claim + active booking uniqueness
-- Run in Supabase → SQL Editor after deploy.
--
-- Closes race windows that PostgREST multi-step claim+insert can still hit under load.

-- ── Atomic claim (single-statement UPDATE … RETURNING) ───────────────────────
CREATE OR REPLACE FUNCTION public.claim_slot_atomic(
  p_date text,
  p_time text,
  p_phone text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_row public.slots%ROWTYPE;
BEGIN
  IF p_date IS NULL OR btrim(p_date) = '' OR p_time IS NULL OR btrim(p_time) = '' THEN
    RETURN NULL;
  END IF;

  UPDATE public.slots
  SET
    "Status" = 'Booked',
    "Booked By" = COALESCE(NULLIF(btrim(p_phone), ''), 'HOLD'),
    "Phone" = COALESCE(NULLIF(btrim(p_phone), ''), 'HOLD')
  WHERE "Date" = btrim(p_date)
    AND "Time" = btrim(p_time)
    AND "Status" = 'Available'
  RETURNING * INTO v_row;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  RETURN to_jsonb(v_row);
END;
$$;

-- ── Release a booked slot (by id, preferred) ─────────────────────────────────
CREATE OR REPLACE FUNCTION public.release_slot_atomic(p_slot_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF p_slot_id IS NULL THEN
    RETURN FALSE;
  END IF;

  UPDATE public.slots
  SET
    "Status" = 'Available',
    "Booked By" = 'EMPTY',
    "Phone" = 'EMPTY',
    "Booking ID" = ''
  WHERE id = p_slot_id
    AND "Status" = 'Booked';

  RETURN FOUND;
END;
$$;

-- ── Release by date/time (+ optional booking id ownership check) ─────────────
CREATE OR REPLACE FUNCTION public.release_slot_by_datetime(
  p_date text,
  p_time text,
  p_booking_id text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  UPDATE public.slots
  SET
    "Status" = 'Available',
    "Booked By" = 'EMPTY',
    "Phone" = 'EMPTY',
    "Booking ID" = ''
  WHERE "Date" = btrim(p_date)
    AND "Time" = btrim(p_time)
    AND "Status" = 'Booked'
    AND (
      p_booking_id IS NULL
      OR btrim(p_booking_id) = ''
      OR "Booking ID" IS NULL
      OR "Booking ID" = ''
      OR "Booking ID" = btrim(p_booking_id)
    );

  RETURN FOUND;
END;
$$;

-- ── Link booking id onto claimed slot ────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.link_slot_booking_atomic(
  p_slot_id uuid,
  p_booking_id text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF p_slot_id IS NULL OR p_booking_id IS NULL OR btrim(p_booking_id) = '' THEN
    RETURN FALSE;
  END IF;

  UPDATE public.slots
  SET "Booking ID" = btrim(p_booking_id)
  WHERE id = p_slot_id
    AND "Status" = 'Booked';

  RETURN FOUND;
END;
$$;

GRANT EXECUTE ON FUNCTION public.claim_slot_atomic(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_slot_atomic(uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_slot_by_datetime(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.link_slot_booking_atomic(uuid, text) TO service_role;

-- Authenticated dashboard service uses service role via API; grant for safety if ever called from SQL.
GRANT EXECUTE ON FUNCTION public.claim_slot_atomic(text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.release_slot_atomic(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.release_slot_by_datetime(text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.link_slot_booking_atomic(uuid, text) TO authenticated;

-- ── Hard uniqueness: one live booking per Date+Time ──────────────────────────
-- Rejected / No-show / Cancelled free the hour for a new booking.
-- If CREATE INDEX fails: you already have two live bookings on the same hour —
-- clean them first, then re-run this section.
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_active_datetime_unique
  ON public.bookings ("Date", "Time")
  WHERE "Status" IN ('Pending', 'Confirmed', 'Reschedule');

-- One live booking per phone per calendar day (matches product rule)
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_active_phone_date_unique
  ON public.bookings ("Phone", "Date")
  WHERE "Status" IN ('Pending', 'Confirmed', 'Reschedule');

COMMENT ON FUNCTION public.claim_slot_atomic IS
  'Atomically claim Available→Booked for Date+Time; NULL if already taken';

COMMENT ON INDEX idx_bookings_active_datetime_unique IS
  'Prevents two live bookings on the same pitch hour even if slot claim is bypassed';

NOTIFY pgrst, 'reload schema';
