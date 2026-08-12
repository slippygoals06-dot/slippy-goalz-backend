-- Owner Quick PIN (hashed) for soft-lock unlock
-- Run in Supabase SQL Editor
-- Credentials stay in env; this table only stores optional PIN hash

CREATE TABLE IF NOT EXISTS owner_settings (
  username TEXT PRIMARY KEY,
  pin_hash TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
