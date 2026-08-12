-- Manual cash ledger (drops, expenses, payouts)
-- Run in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS cash_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  amount NUMERIC NOT NULL,
  entry_type TEXT NOT NULL
    CHECK (entry_type IN ('cash_drop', 'expense', 'payout')),
  reason TEXT NOT NULL,
  logged_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT cash_ledger_amount_sign CHECK (
    (entry_type = 'cash_drop' AND amount > 0)
    OR (entry_type IN ('expense', 'payout') AND amount < 0)
  )
);

CREATE INDEX IF NOT EXISTS idx_cash_ledger_created_at ON cash_ledger(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cash_ledger_entry_type ON cash_ledger(entry_type);
