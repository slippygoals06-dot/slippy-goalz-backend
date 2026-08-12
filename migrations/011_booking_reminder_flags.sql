-- Booking reminder tracking (24h + urgent windows)
-- Run in Supabase SQL Editor
-- Leaves legacy reminder_sent untouched

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS reminder_urgent_sent BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN bookings.reminder_24h_sent IS
  'True after appointment_reminder_24h WhatsApp was sent';

COMMENT ON COLUMN bookings.reminder_urgent_sent IS
  'True after appointment_reminder_urgent WhatsApp was sent';
