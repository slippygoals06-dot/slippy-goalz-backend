-- Unique Date+Time on slots (backup safety net for atomic claim logic)
-- Run in Supabase SQL Editor AFTER relying on race-safe booking.
--
-- If this fails with "duplicate key", clear duplicates first, e.g.:
--   DELETE FROM slots a
--   USING slots b
--   WHERE a.ctid < b.ctid
--     AND a."Date" = b."Date"
--     AND a."Time" = b."Time";

CREATE UNIQUE INDEX IF NOT EXISTS idx_slots_date_time_unique
  ON slots ("Date", "Time");
