-- Link chat sessions to bookings for owner conversation history
-- Run in Supabase SQL Editor

ALTER TABLE chat_sessions
  ADD COLUMN IF NOT EXISTS booking_id TEXT;

CREATE INDEX IF NOT EXISTS idx_chat_sessions_booking_id
  ON chat_sessions (booking_id);
