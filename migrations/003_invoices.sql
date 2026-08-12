-- Booking amount + invoices for completed repairs
-- Run in Supabase SQL Editor
-- Do NOT backfill amount on existing bookings (leave NULL)

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS amount NUMERIC;

CREATE TABLE IF NOT EXISTS invoices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id TEXT NOT NULL,
  amount NUMERIC NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'unpaid'
    CHECK (status IN ('paid', 'unpaid')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  invoice_number TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_invoices_booking_id ON invoices(booking_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices(created_at DESC);
