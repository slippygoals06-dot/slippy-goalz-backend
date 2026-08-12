-- Repair tickets with intake/pickup photo + checklist proof
-- Run in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_name TEXT NOT NULL,
  phone TEXT NOT NULL,
  device TEXT,
  issue TEXT,
  status TEXT NOT NULL DEFAULT 'received'
    CHECK (status IN ('received', 'diagnosing', 'repairing', 'ready', 'collected')),
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_media (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK (stage IN ('intake', 'pickup')),
  photo_url TEXT NOT NULL,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_checklist (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK (stage IN ('intake', 'pickup')),
  item_label TEXT NOT NULL,
  checked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_ticket_media_ticket_id ON ticket_media(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_checklist_ticket_id ON ticket_checklist(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at DESC);

-- Supabase Storage: create bucket "ticket-photos" (public read) in Dashboard > Storage
-- Policy example for authenticated uploads:
--   INSERT: auth.role() = 'authenticated' OR use service role from backend
--   SELECT: public (or authenticated)
