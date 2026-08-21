-- Slippy Goalz Arena — deposit tracking + No-show audit action
-- Run in Supabase → SQL Editor after deploying Phase 4 backend.
-- Status "No-show" is free text on bookings (no enum). Deposit columns track advances.

ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS deposit_amount NUMERIC;

ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS deposit_paid BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN bookings.deposit_amount IS
  'Optional advance / deposit asked for the session (Rs)';

COMMENT ON COLUMN bookings.deposit_paid IS
  'True when deposit_amount has been collected';

-- Allow audit action no_show (drop old check, recreate with new value)
DO $$
DECLARE
  cname TEXT;
BEGIN
  SELECT conname INTO cname
  FROM pg_constraint
  WHERE conrelid = 'public.audit_events'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%confirmed%';
  IF cname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.audit_events DROP CONSTRAINT %I', cname);
  END IF;

  ALTER TABLE public.audit_events
    ADD CONSTRAINT audit_events_action_check
    CHECK (action IN (
      'confirmed',
      'rejected',
      'deleted',
      'payment_changed',
      'completed_invoiced',
      'invoice_status_changed',
      'no_show'
    ));
EXCEPTION
  WHEN undefined_table THEN NULL;
  WHEN duplicate_object THEN NULL;
END $$;

NOTIFY pgrst, 'reload schema';
