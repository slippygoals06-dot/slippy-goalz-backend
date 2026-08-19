-- Add per-staff permission lists if staff_users already exists without them.

ALTER TABLE public.staff_users
  ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '["dashboard","bookings","slots","leads","waitlist","chats"]'::jsonb;

NOTIFY pgrst, 'reload schema';
