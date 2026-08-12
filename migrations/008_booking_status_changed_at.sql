-- SLA / time-in-status: stamp when booking Status changes
-- Run in Supabase SQL Editor
-- Covers every UPDATE path (confirm/reject, complete, chat cancel/reschedule, general PUT)

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ;

-- Backfill existing rows so Pending SLA can be evaluated after deploy
UPDATE bookings
SET status_changed_at = NOW()
WHERE status_changed_at IS NULL;

ALTER TABLE bookings
  ALTER COLUMN status_changed_at SET DEFAULT NOW();

CREATE OR REPLACE FUNCTION set_booking_status_changed_at()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW."Status" IS DISTINCT FROM OLD."Status" THEN
    NEW.status_changed_at := NOW();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bookings_status_changed_at ON bookings;

CREATE TRIGGER trg_bookings_status_changed_at
  BEFORE UPDATE ON bookings
  FOR EACH ROW
  EXECUTE PROCEDURE set_booking_status_changed_at();
