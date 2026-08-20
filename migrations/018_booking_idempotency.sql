-- Slippy Goalz Arena — booking idempotency + WhatsApp confirm flag
-- Run in Supabase → SQL Editor after deploying Phase 2 backend.

ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS confirmation_wa_sent BOOLEAN NOT NULL DEFAULT FALSE;

-- One booking per public/chat idempotency key (NULLs allowed multiple times in Postgres UNIQUE)
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_idempotency_key_unique
  ON public.bookings (idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMENT ON COLUMN bookings.idempotency_key IS
  'Client Idempotency-Key for public/chat create; prevents double-submit duplicates';

COMMENT ON COLUMN bookings.confirmation_wa_sent IS
  'True after booking_confirmed WhatsApp template was sent on Confirm';

NOTIFY pgrst, 'reload schema';
