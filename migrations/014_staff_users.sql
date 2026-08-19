-- Extra dashboard logins (staff). Owner still lives in env (OWNER_USERNAME).
-- Run in Supabase SQL Editor, then Retry from Settings → Team if needed.

CREATE TABLE IF NOT EXISTS staff_users (
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff' CHECK (role = 'staff'),
  is_active BOOLEAN NOT NULL DEFAULT true,
  permissions JSONB NOT NULL DEFAULT '["dashboard","bookings","slots","leads","waitlist","chats"]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.staff_users
  ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '["dashboard","bookings","slots","leads","waitlist","chats"]'::jsonb;

ALTER TABLE public.staff_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.staff_users FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.staff_users FROM anon;
REVOKE ALL ON TABLE public.staff_users FROM authenticated;

NOTIFY pgrst, 'reload schema';
