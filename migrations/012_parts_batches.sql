-- Parts batch tracking + blockchain-linked part events
-- Run in Supabase SQL Editor
--
-- repair_id soft-references bookings."Booking ID" (no dedicated repairs table).

CREATE TABLE IF NOT EXISTS parts_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_name TEXT NOT NULL,
  batch_number TEXT NOT NULL UNIQUE,
  part_type TEXT NOT NULL,
  received_date DATE NOT NULL DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS part_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id UUID NOT NULL REFERENCES parts_batches(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL
    CHECK (event_type IN ('received', 'installed')),
  repair_id TEXT,
  location TEXT,
  previous_hash TEXT NOT NULL,
  data_hash TEXT NOT NULL,
  tx_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parts_batches_batch_number ON parts_batches(batch_number);
CREATE INDEX IF NOT EXISTS idx_part_events_batch_id ON part_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_part_events_repair_id ON part_events(repair_id);
CREATE INDEX IF NOT EXISTS idx_part_events_created_at ON part_events(created_at DESC);

-- RLS lockdown (service_role bypasses; anon/authenticated denied)
ALTER TABLE public.parts_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.parts_batches FORCE ROW LEVEL SECURITY;
ALTER TABLE public.part_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.part_events FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.parts_batches FROM anon;
REVOKE ALL ON TABLE public.parts_batches FROM authenticated;
REVOKE ALL ON TABLE public.part_events FROM anon;
REVOKE ALL ON TABLE public.part_events FROM authenticated;
