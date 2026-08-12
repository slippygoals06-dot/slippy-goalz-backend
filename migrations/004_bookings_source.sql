-- Lead source attribution on bookings (from public /book "heardFrom")
-- Run in Supabase SQL Editor

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS "Source" TEXT;
